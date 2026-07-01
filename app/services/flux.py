import httpx
import base64
import uuid
from pathlib import Path
from app.core.config import settings


FLUX_API_URL = "https://api.us1.bfl.ai/v1"


def _load_image_as_base64(image_path: str) -> str:
    """load local image file and encode to base64"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


async def generate_with_flux(
    master_image_path: str,
    prompt: str,
    session_id: str
) -> str:
    """
    call flux kontext pro to edit the master product image.
    returns local path of the saved raw output image.
    raises on failure — caller handles retry.
    """
    if not Path(master_image_path).exists():
        raise FileNotFoundError(f"Master image not found: {master_image_path}")

    image_b64 = _load_image_as_base64(master_image_path)

    async with httpx.AsyncClient(timeout=120) as http:

        # step 1: submit job to flux kontext
        resp = await http.post(
            f"{FLUX_API_URL}/flux-kontext-pro",
            headers={
                "x-key": settings.flux_api_key,
                "Content-Type": "application/json"
            },
            json={
                "prompt": prompt,
                "input_image": image_b64,
                "output_format": "png",
                "safety_tolerance": 2,
                "prompt_upsampling": False
            }
        )
        resp.raise_for_status()
        job_data = resp.json()
        flux_job_id = job_data.get("id")

        if not flux_job_id:
            raise ValueError(f"Flux did not return a job id: {job_data}")

        # step 2: poll for result (flux is async internally)
        import asyncio
        for attempt in range(30):  # poll up to 30 times, 4s apart = 2 min max
            await asyncio.sleep(4)

            poll = await http.get(
                f"{FLUX_API_URL}/get_result",
                headers={"x-key": settings.flux_api_key},
                params={"id": flux_job_id}
            )
            poll.raise_for_status()
            result = poll.json()

            status = result.get("status")

            if status == "Ready":
                image_url = result["result"]["sample"]
                break
            elif status in ("Error", "Failed", "Request Moderated", "Content Moderated"):
                raise ValueError(f"Flux job failed with status: {status}")
            # still pending — keep polling

        else:
            raise TimeoutError("Flux job did not complete within 2 minutes")

    # step 3: download the generated image
    async with httpx.AsyncClient(timeout=30) as http:
        img_resp = await http.get(image_url)
        img_resp.raise_for_status()
        raw_bytes = img_resp.content

    # step 4: save locally
    output_dir = settings.get_image_dir() / "generated"
    output_dir.mkdir(exist_ok=True)

    raw_path = output_dir / f"{session_id}_raw.png"
    raw_path.write_bytes(raw_bytes)

    return str(raw_path), flux_job_id
