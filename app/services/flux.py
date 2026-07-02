import logging
import httpx
import base64
import uuid
import io
from PIL import Image
from pathlib import Path
from app.core.config import settings

logger = logging.getLogger("app.services.flux")

FLUX_API_URL = "https://api.bfl.ai/v1"


def _load_image_as_base64(image_path: str) -> str:
    """load local image file, scale it down to max 1MP, and encode to base64"""
    logger.debug("Loading and processing image: %s", image_path)
    with Image.open(image_path) as img:
        width, height = img.size
        num_pixels = width * height
        logger.debug("Original image dimensions: width=%d, height=%d (%.2f MP)", width, height, num_pixels / 1_000_000)
        
        if num_pixels > 1_000_000:
            scale_factor = (1_000_000 / num_pixels) ** 0.5
            new_w = int(width * scale_factor)
            new_h = int(height * scale_factor)
            # Ensure multiples of 32
            new_w = (new_w // 32) * 32
            new_h = (new_h // 32) * 32
            logger.info("Scaling down image to 1MP: resizing from %dx%d to %dx%d", width, height, new_w, new_h)
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            width, height = new_w, new_h
        else:
            new_w = (width // 32) * 32
            new_h = (height // 32) * 32
            if new_w != width or new_h != height:
                logger.info("Adjusting image to multiples of 32: from %dx%d to %dx%d", width, height, new_w, new_h)
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                width, height = new_w, new_h
        
        out_buf = io.BytesIO()
        img.save(out_buf, format="PNG")
        img_bytes = out_buf.getvalue()

    res = base64.b64encode(img_bytes).decode("utf-8")
    logger.debug("Image base64 encoding complete. Size: %d characters", len(res))
    return res, width, height


async def generate_with_flux(
    master_image_path: str,
    prompt: str,
    session_id: str
) -> str:
    """
    call flux-2-pro to edit the master product image.
    returns local path of the saved raw output image.
    raises on failure — caller handles retry.
    """
    logger.info("Starting Flux context image generation for session: %s", session_id)
    logger.debug("Master image path: %s", master_image_path)
    logger.debug("Prompt (length=%d): %s", len(prompt), prompt)

    if not Path(master_image_path).exists():
        logger.error("Master image file not found: %s", master_image_path)
        raise FileNotFoundError(f"Master image not found: {master_image_path}")

    image_b64, w, h = _load_image_as_base64(master_image_path)

    async with httpx.AsyncClient(timeout=120) as http:

        # step 1: submit job to flux-2-pro
        logger.info("Flux Step 1: Submitting generation job to BFL API (flux-2-pro)...")
        resp = await http.post(
            f"{FLUX_API_URL}/flux-2-pro",
            headers={
                "x-key": settings.flux_api_key,
                "Content-Type": "application/json"
            },
            json={
                "prompt": prompt,
                "input_image": image_b64,
                "width": w,
                "height": h,
                "output_format": "png",
                "safety_tolerance": 2,
                "prompt_upsampling": False
            }
        )
        resp.raise_for_status()
        job_data = resp.json()
        flux_job_id = job_data.get("id")
        logger.info("Flux job submitted successfully. Job ID: %s", flux_job_id)

        if not flux_job_id:
            logger.error("No job ID returned in API response: %s", job_data)
            raise ValueError(f"Flux did not return a job id: {job_data}")

        # step 2: poll for result (flux is async internally)
        logger.info("Flux Step 2: Polling for job completion...")
        polling_url = job_data.get("polling_url")
        logger.debug("BFL returned polling_url: %s", polling_url)
        
        import asyncio
        for attempt in range(30):  # poll up to 30 times, 4s apart = 2 min max
            logger.debug("Polling attempt %d/30 for Flux job: %s", attempt + 1, flux_job_id)
            await asyncio.sleep(4)

            if polling_url:
                poll = await http.get(
                    polling_url,
                    headers={"x-key": settings.flux_api_key}
                )
            else:
                poll = await http.get(
                    f"{FLUX_API_URL}/get_result",
                    headers={"x-key": settings.flux_api_key},
                    params={"id": flux_job_id}
                )
            poll.raise_for_status()
            result = poll.json()

            status = result.get("status")
            logger.debug("Polling status: %s", status)

            if status == "Ready":
                image_url = result["result"]["sample"]
                logger.info("Flux job ready! Result image URL: %s", image_url)
                break
            elif status in ("Error", "Failed", "Request Moderated", "Content Moderated"):
                logger.error("Flux job failed with terminal status: %s. Result content: %s", status, result)
                raise ValueError(f"Flux job failed with status: {status}")
            # still pending — keep polling

        else:
            logger.error("Flux job timed out after 30 attempts (120s)")
            raise TimeoutError("Flux job did not complete within 2 minutes")

    # step 3: download the generated image
    logger.info("Flux Step 3: Downloading generated raw image...")
    async with httpx.AsyncClient(timeout=30) as http:
        img_resp = await http.get(image_url)
        img_resp.raise_for_status()
        raw_bytes = img_resp.content
        logger.debug("Downloaded raw image size: %d bytes", len(raw_bytes))

    # step 4: save locally
    output_dir = settings.get_image_dir() / "generated"
    output_dir.mkdir(exist_ok=True)

    raw_path = output_dir / f"{session_id}_raw.png"
    raw_path.write_bytes(raw_bytes)
    logger.info("Flux generation process complete. Saved raw image to: %s", raw_path)

    return str(raw_path), flux_job_id

