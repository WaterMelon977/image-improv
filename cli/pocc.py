#!/usr/bin/env python3
"""
pocc — Product Creative CLI

Usage:
  pocc ingest --url https://spicenfood.com
  pocc campaign --company spicen --topic "your topic"
  pocc select --session <session_id> --theme <number>
  pocc image --session <session_id> --idea <number>
  pocc status --session <session_id>
  pocc list
"""

import click
import httpx
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.panel import Panel
from rich.text import Text
from rich import box

console = Console(stderr=False)
BASE_URL = "http://localhost:8000/api/v1"
LOG_FILE = Path("./pocc.log")


# ============================================================
# Logging setup
# ============================================================

def _setup_logger(command: str) -> logging.Logger:
    """
    Return a logger scoped to the given command name.
    - Writes structured lines to ./pocc.log (append)
    - Also writes to stderr for user feedback
    Each logger name is unique per command to avoid duplicate handlers.
    """
    logger_name = f"pocc.{command}"
    logger = logging.getLogger(logger_name)

    # Only configure once per process per logger name
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(command)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # File handler (append mode)
    fh = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    # Stderr handler for user feedback (DEBUG+ to console)
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.DEBUG)
    sh.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(sh)

    return logger


class _CommandAdapter(logging.LoggerAdapter):
    """Injects {command} into every log record."""
    def process(self, msg, kwargs):
        kwargs.setdefault("extra", {})
        kwargs["extra"]["command"] = self.extra["command"]
        return msg, kwargs


def get_logger(command: str) -> logging.LoggerAdapter:
    logger = _setup_logger(command)
    return _CommandAdapter(logger, extra={"command": command})


# ============================================================
# HTTP helpers
# ============================================================

def post(endpoint: str, data: dict, logger: logging.LoggerAdapter | None = None) -> dict:
    t0 = time.monotonic()
    try:
        r = httpx.post(f"{BASE_URL}{endpoint}", json=data, timeout=180)
        r.raise_for_status()
        elapsed = round(time.monotonic() - t0, 2)
        if logger:
            logger.info(f"POST {endpoint} → {r.status_code} ({elapsed}s)")
        return r.json()
    except httpx.TimeoutException:
        msg = f"Request timed out after 180s: POST {endpoint}"
        if logger:
            logger.error(msg)
        console.print(f"[red]Timeout:[/red] {msg}")
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        body = e.response.text
        if logger:
            logger.error(
                f"HTTP {e.response.status_code} on POST {endpoint}: {body[:500]}"
            )
        console.print(f"[red]Error {e.response.status_code}:[/red] {body}")
        sys.exit(1)
    except httpx.ConnectError:
        msg = "Cannot connect to server. Is it running? `uvicorn app.main:app --reload`"
        if logger:
            logger.error(msg)
        console.print(f"[red]{msg}[/red]")
        sys.exit(1)


def get(endpoint: str, logger: logging.LoggerAdapter | None = None) -> dict:
    t0 = time.monotonic()
    try:
        r = httpx.get(f"{BASE_URL}{endpoint}", timeout=30)
        r.raise_for_status()
        elapsed = round(time.monotonic() - t0, 2)
        if logger:
            logger.debug(f"GET {endpoint} → {r.status_code} ({elapsed}s)")
        return r.json()
    except httpx.TimeoutException:
        msg = f"Request timed out after 30s: GET {endpoint}"
        if logger:
            logger.error(msg)
        console.print(f"[red]Timeout:[/red] {msg}")
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        body = e.response.text
        if logger:
            logger.error(
                f"HTTP {e.response.status_code} on GET {endpoint}: {body[:500]}"
            )
        console.print(f"[red]Error {e.response.status_code}:[/red] {body}")
        sys.exit(1)


def poll_status(
    session_id: str, logger: logging.LoggerAdapter | None = None
) -> str:
    """Poll job status every 3s with a progress bar until done or failed."""
    terminal_states = {"done", "failed", "done_no_logo"}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[cyan]{task.fields[status]}"),
        console=console
    ) as progress:
        task = progress.add_task(
            "Generating image...",
            total=None,
            status="starting"
        )

        for attempt in range(1, 61):   # max 3 min polling
            time.sleep(3)
            if logger:
                logger.info(f"Polling Flux job: attempt {attempt}/60")
            data = get(f"/jobs/{session_id}/status", logger=logger)
            status = data.get("status", "unknown")
            progress.update(task, status=status)

            if status in terminal_states:
                if logger:
                    logger.info(f"Flux job reached terminal state: {status}")
                return status
            if status == "failed":
                error = data.get("error", "unknown error")
                if logger:
                    logger.error(f"Flux job failed: {error}")
                console.print(f"\n[red]Job failed:[/red] {error}")
                sys.exit(1)

        msg = "Timed out waiting for image generation after 60 attempts (180s)"
        if logger:
            logger.error(msg)
        console.print(f"\n[red]{msg}[/red]")
        sys.exit(1)


# ============================================================
# Main CLI group
# ============================================================

@click.group()
def cli():
    """pocc — Product Creative Platform CLI"""
    pass


# ============================================================
# pocc ingest --url <url>
# ============================================================

@cli.command()
@click.option("--url", required=True, help="Company website URL to ingest")
def ingest(url: str):
    """Ingest a company URL — scrape, extract intelligence, save to DB"""
    logger = get_logger("ingest")
    t0 = time.monotonic()

    logger.info(f"Ingesting URL: {url}")
    console.print(f"\n[bold]Ingesting:[/bold] {url}")
    console.print("[dim]This takes 30-60 seconds (Firecrawl + AI extraction)...[/dim]\n")

    with console.status("[cyan]Scraping website...[/cyan]"):
        result = post("/ingest", {"url": url}, logger=logger)

    if result.get("status") == "already_exists":
        logger.info(
            f"Already ingested: company_name={result['company_name']} slug={result['company_slug']}"
        )
        console.print(Panel(
            f"[yellow]Already ingested.[/yellow]\n\n"
            f"Company: [bold]{result['company_name']}[/bold]\n"
            f"Reference slug: [cyan]{result['company_slug']}[/cyan]",
            title="Existing Company",
            box=box.ROUNDED
        ))
        return

    # Success
    slug = result["company_slug"]
    name = result["company_name"]
    products_found = result.get("products_found", 0)
    logo_saved = result.get("logo_saved")
    logo_url = result.get("logo_url")

    logger.info(f"Company saved: {name} (slug={slug})")
    logger.info(f"Products saved: {products_found} products")

    if logo_saved:
        logger.info(f"Logo downloaded successfully (url={logo_url})")
    elif logo_url:
        logger.warning(f"Logo download failed: url={logo_url}")
    else:
        logger.warning("Logo not found: no logo URL detected on the page")

    colors = result.get("brand_colors", {})
    color_str = " | ".join(f"{k}: {v}" for k, v in colors.items() if v)

    if logo_saved:
        logo_status = "[green]saved[/green]"
    elif logo_url:
        logo_status = f"[yellow]download failed[/yellow] (URL: {logo_url})"
    else:
        logo_status = "[red]not found[/red]"

    elapsed = round(time.monotonic() - t0, 2)
    logger.info(f"Command completed in {elapsed}s")

    console.print(Panel(
        f"[green]Company saved.[/green]\n\n"
        f"Name:      [bold]{name}[/bold]\n"
        f"Industry:  {result.get('industry', 'N/A')}\n"
        f"Products:  {products_found} found\n"
        f"Logo:      {logo_status}\n"
        f"Colors:    {color_str or 'not detected'}\n\n"
        f"[bold cyan]Reference this company as:[/bold cyan] [yellow]{slug}[/yellow]\n\n"
        f"Next: [dim]pocc campaign --company {slug} --topic \"your topic\"[/dim]",
        title="Company Ingested",
        box=box.ROUNDED
    ))


# ============================================================
# pocc campaign --company <slug> --topic <topic>
# ============================================================

@cli.command()
@click.option("--company", required=True, help="Company slug from ingestion")
@click.option("--topic", required=True, help="Campaign topic e.g. 'summer pool party'")
def campaign(company: str, topic: str):
    """Generate 5 campaign themes for a company and topic"""
    logger = get_logger("campaign")
    t0 = time.monotonic()

    logger.info(f"Generating themes for company={company}, topic={topic}")
    console.print(f"\n[bold]Generating campaign themes[/bold] for [cyan]{company}[/cyan]")
    console.print(f"Topic: [yellow]{topic}[/yellow]\n")

    with console.status("[cyan]Thinking up campaign ideas...[/cyan]"):
        result = post("/campaign", {"company_slug": company, "topic": topic}, logger=logger)

    session_id = result["session_id"]
    themes = result["themes"]

    logger.info(f"Themes generated: session_id={session_id}, count={len(themes)}")

    table = Table(title=f"Campaign Themes — {topic}", box=box.ROUNDED)
    table.add_column("#", style="cyan", width=3)
    table.add_column("Theme", style="bold")
    table.add_column("Concept", style="dim")
    table.add_column("Product", style="green")
    table.add_column("Mood", style="yellow")

    for t in themes:
        table.add_row(
            str(t["number"]),
            t["theme_name"],
            t["concept"],
            t["best_product_name"],
            t.get("mood", "")
        )

    console.print(table)
    console.print(f"\n[dim]Session ID:[/dim] [cyan]{session_id}[/cyan]")
    console.print(
        f"\nNext: [dim]pocc select --session {session_id} --theme <number>[/dim]\n"
    )

    elapsed = round(time.monotonic() - t0, 2)
    logger.info(f"Command completed in {elapsed}s")


# ============================================================
# pocc select --session <id> --theme <n>
# ============================================================

@cli.command()
@click.option("--session", "session_id", required=True, help="Session ID from campaign command")
@click.option("--theme", "theme_number", required=True, type=int, help="Theme number 1-5")
def select(session_id: str, theme_number: int):
    """Select a campaign theme and generate 3 image ideas"""
    logger = get_logger("select")
    t0 = time.monotonic()

    logger.info(f"Selecting theme {theme_number} from session={session_id}")
    console.print(f"\n[bold]Selecting theme {theme_number}[/bold]\n")

    with console.status("[cyan]Generating image ideas...[/cyan]"):
        result = post("/select", {
            "session_id": session_id,
            "theme_number": theme_number
        }, logger=logger)

    theme = result["selected_theme"]
    product = result["selected_product"]
    has_image = result["has_master_image"]
    ideas = result["image_ideas"]

    logger.info(
        f"Theme selected: theme_name={theme['theme_name']}, product={product}"
    )
    logger.info(f"Image ideas generated: {len(ideas)} ideas")

    if not has_image:
        logger.warning(f"No master image found for product: {product}")

    console.print(Panel(
        f"Theme:   [bold]{theme['theme_name']}[/bold]\n"
        f"Concept: {theme['concept']}\n"
        f"Angle:   {theme['campaign_angle']}\n"
        f"Mood:    {theme.get('mood', 'N/A')}\n\n"
        f"Product: [green]{product}[/green]"
        + ("\n[yellow]Warning: No master image found for this product.[/yellow]" if not has_image else ""),
        title="Selected Theme",
        box=box.ROUNDED
    ))

    table = Table(title="Image Ideas", box=box.SIMPLE)
    table.add_column("#", style="cyan", width=3)
    table.add_column("Scene Idea", style="white")

    for idea in ideas:
        table.add_row(str(idea["number"]), idea["idea"])

    console.print(table)
    console.print(f"\n[dim]Session ID:[/dim] [cyan]{session_id}[/cyan]")
    console.print(
        f"\nNext: [dim]pocc image --session {session_id} --idea <number>[/dim]\n"
    )

    elapsed = round(time.monotonic() - t0, 2)
    logger.info(f"Command completed in {elapsed}s")


# ============================================================
# pocc image --session <id> --idea <n>
# ============================================================

@cli.command()
@click.option("--session", "session_id", required=True, help="Session ID")
@click.option("--idea", "idea_number", required=True, type=int, help="Idea number 1-3")
def image(session_id: str, idea_number: int):
    """Generate final product image using Flux + logo placement"""
    logger = get_logger("image")
    t0 = time.monotonic()

    logger.info(f"Generating image: session={session_id}, idea={idea_number}")
    console.print(f"\n[bold]Generating image[/bold] — idea {idea_number}\n")

    logger.info("Submitting to Flux API...")
    with console.status("[cyan]Submitting to Flux...[/cyan]"):
        result = post(
            f"/image?idea_number={idea_number}",
            {"session_id": session_id},
            logger=logger
        )

    # Synchronous fast path
    if result.get("status") == "done":
        logger.info(
            f"Flux completed synchronously: flux_job_id={result.get('flux_job_id', 'N/A')}"
        )
        _log_and_print_image_result(result, session_id, logger)
        elapsed = round(time.monotonic() - t0, 2)
        logger.info(f"Command completed in {elapsed}s")
        return

    # Poll
    console.print("[dim]Flux is processing...[/dim]")
    final_status = poll_status(session_id, logger=logger)

    if final_status in ("done", "done_no_logo"):
        status_data = get(f"/jobs/{session_id}/status", logger=logger)
        logger.info(f"Flux completed: status={final_status}")
        _log_and_print_image_result(result, session_id, logger)

    elapsed = round(time.monotonic() - t0, 2)
    logger.info(f"Command completed in {elapsed}s")


def _log_and_print_image_result(
    result: dict, session_id: str, logger: logging.LoggerAdapter
):
    placement = result.get("logo_placement", {})
    colors = result.get("dominant_colors", [])
    image_url = result.get("image_url")
    corner = placement.get("corner", "N/A")

    logger.info(f"Logo placement analyzed: corner={corner}")

    if result.get("status") == "done":
        logger.info(f"Image generation complete: {image_url}")
    else:
        logger.info(f"Image generation complete (no logo): {image_url}")

    console.print(Panel(
        f"[green]Image generated.[/green]\n\n"
        f"Idea used:     {result.get('selected_idea', '')}\n\n"
        f"Logo placed:   [cyan]{corner}[/cyan]\n"
        f"Colors found:  {' '.join(colors[:5]) if colors else 'N/A'}\n\n"
        f"[bold]Final image:[/bold]\n[yellow]{image_url}[/yellow]\n\n"
        f"[dim]Raw (no logo):[/dim]\n[dim]{result.get('raw_url')}[/dim]",
        title="Done",
        box=box.ROUNDED
    ))
    console.print("\nOpen in browser or download:")
    console.print(f"  [bold cyan]{image_url}[/bold cyan]\n")


# ============================================================
# pocc status --session <id>
# ============================================================

@cli.command()
@click.option("--session", "session_id", required=True)
def status(session_id: str):
    """Check status of a generation job"""
    logger = get_logger("status")
    t0 = time.monotonic()

    logger.info(f"Checking status: session={session_id}")
    result = get(f"/jobs/{session_id}/status", logger=logger)

    job_status = result["status"]
    logger.info(f"Status retrieved: {job_status}")

    color = "green" if job_status == "done" else "yellow"
    console.print(f"\nSession: [cyan]{session_id}[/cyan]")
    console.print(f"Status:  [{color}]{job_status}[/{color}]")

    if result.get("error"):
        logger.error(f"Job error: {result['error']}")
        console.print(f"Error:   [red]{result['error']}[/red]")

    console.print()
    elapsed = round(time.monotonic() - t0, 2)
    logger.info(f"Command completed in {elapsed}s")


# ============================================================
# pocc list
# ============================================================

@cli.command("list")
def list_companies():
    """List all ingested companies"""
    logger = get_logger("list")
    t0 = time.monotonic()

    logger.info("Listing companies")
    result = get("/companies", logger=logger)

    if not result:
        logger.info("No companies ingested yet")
        console.print("[yellow]No companies ingested yet.[/yellow]")
        return

    logger.info(f"Companies found: {len(result)}")

    table = Table(title="Ingested Companies", box=box.ROUNDED)
    table.add_column("Slug", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Industry")
    table.add_column("Products", justify="right")

    for c in result:
        table.add_row(c["slug"], c["name"], c.get("industry", ""), str(c["products"]))

    console.print(table)
    elapsed = round(time.monotonic() - t0, 2)
    logger.info(f"Command completed in {elapsed}s")


if __name__ == "__main__":
    cli()
