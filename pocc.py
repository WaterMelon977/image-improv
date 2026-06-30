#!/usr/bin/env python3
"""
pocc — Product Creative CLI

Usage:
  pocc --url https://spicenfood.com
  pocc --campaign "summer pool party" --company spicen_foods
  pocc --select 2 --session <session_id>
  pocc --image 3 --session <session_id>
  pocc --list
  pocc --status <session_id>
"""

import click
import httpx
import time
import sys
import json
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.panel import Panel
from rich.text import Text
from rich import box

console = Console()
BASE_URL = "http://localhost:8000/api/v1"


# -- http helpers --

def post(endpoint: str, data: dict) -> dict:
    try:
        r = httpx.post(f"{BASE_URL}{endpoint}", json=data, timeout=180)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        console.print(f"[red]Error {e.response.status_code}:[/red] {e.response.text}")
        sys.exit(1)
    except httpx.ConnectError:
        console.print("[red]Cannot connect to server.[/red] Is it running? `uvicorn app.main:app --reload`")
        sys.exit(1)


def get(endpoint: str) -> dict:
    try:
        r = httpx.get(f"{BASE_URL}{endpoint}", timeout=30)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        console.print(f"[red]Error {e.response.status_code}:[/red] {e.response.text}")
        sys.exit(1)


def poll_status(session_id: str) -> str:
    """poll job status every 3s with a progress bar until done or failed"""
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

        for _ in range(60):   # max 3 min polling
            time.sleep(3)
            data = get(f"/jobs/{session_id}/status")
            status = data.get("status", "unknown")
            progress.update(task, status=status)

            if status in terminal_states:
                return status
            if status == "failed":
                error = data.get("error", "unknown error")
                console.print(f"\n[red]Job failed:[/red] {error}")
                sys.exit(1)

        console.print("\n[red]Timed out waiting for image generation.[/red]")
        sys.exit(1)


# -- main cli group --

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
    console.print(f"\n[bold]Ingesting:[/bold] {url}")
    console.print("[dim]This takes 30-60 seconds (Firecrawl + AI extraction)...[/dim]\n")

    with console.status("[cyan]Scraping website...[/cyan]"):
        result = post("/ingest", {"url": url})

    if result.get("status") == "already_exists":
        console.print(Panel(
            f"[yellow]Already ingested.[/yellow]\n\n"
            f"Company: [bold]{result['company_name']}[/bold]\n"
            f"Reference slug: [cyan]{result['company_slug']}[/cyan]",
            title="Existing Company",
            box=box.ROUNDED
        ))
        return

    # success panel
    colors = result.get("brand_colors", {})
    color_str = " | ".join(f"{k}: {v}" for k, v in colors.items() if v)

    console.print(Panel(
        f"[green]Company saved.[/green]\n\n"
        f"Name:      [bold]{result['company_name']}[/bold]\n"
        f"Industry:  {result.get('industry', 'N/A')}\n"
        f"Products:  {result.get('products_found', 0)} found\n"
        f"Logo:      {'saved' if result.get('logo_saved') else 'not found'}\n"
        f"Colors:    {color_str or 'not detected'}\n\n"
        f"[bold cyan]Reference this company as:[/bold cyan] [yellow]{result['company_slug']}[/yellow]\n\n"
        f"Next: [dim]pocc campaign --company {result['company_slug']} --topic \"your topic\"[/dim]",
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
    console.print(f"\n[bold]Generating campaign themes[/bold] for [cyan]{company}[/cyan]")
    console.print(f"Topic: [yellow]{topic}[/yellow]\n")

    with console.status("[cyan]Thinking up campaign ideas...[/cyan]"):
        result = post("/campaign", {"company_slug": company, "topic": topic})

    session_id = result["session_id"]
    themes = result["themes"]

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


# ============================================================
# pocc select --session <id> --theme <n>
# ============================================================

@cli.command()
@click.option("--session", "session_id", required=True, help="Session ID from campaign command")
@click.option("--theme", "theme_number", required=True, type=int, help="Theme number 1-5")
def select(session_id: str, theme_number: int):
    """Select a campaign theme and generate 3 image ideas"""
    console.print(f"\n[bold]Selecting theme {theme_number}[/bold]\n")

    with console.status("[cyan]Generating image ideas...[/cyan]"):
        result = post("/select", {
            "session_id": session_id,
            "theme_number": theme_number
        })

    theme = result["selected_theme"]
    product = result["selected_product"]
    has_image = result["has_master_image"]

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

    ideas = result["image_ideas"]
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


# ============================================================
# pocc image --session <id> --idea <n>
# ============================================================

@cli.command()
@click.option("--session", "session_id", required=True, help="Session ID")
@click.option("--idea", "idea_number", required=True, type=int, help="Idea number 1-3")
def image(session_id: str, idea_number: int):
    """Generate final product image using Flux + logo placement"""
    console.print(f"\n[bold]Generating image[/bold] — idea {idea_number}\n")

    # submit generation (this kicks off the async work inside fastapi)
    with console.status("[cyan]Submitting to Flux...[/cyan]"):
        result = post(
            f"/image?idea_number={idea_number}",
            {"session_id": session_id}
        )

    # if it completed synchronously (fast path)
    if result.get("status") == "done":
        _print_image_result(result, session_id)
        return

    # otherwise poll
    console.print("[dim]Flux is processing...[/dim]")
    final_status = poll_status(session_id)

    if final_status in ("done", "done_no_logo"):
        # fetch final result
        status_data = get(f"/jobs/{session_id}/status")
        _print_image_result(result, session_id)


def _print_image_result(result: dict, session_id: str):
    placement = result.get("logo_placement", {})
    colors = result.get("dominant_colors", [])

    console.print(Panel(
        f"[green]Image generated.[/green]\n\n"
        f"Idea used:     {result.get('selected_idea', '')}\n\n"
        f"Logo placed:   [cyan]{placement.get('corner', 'N/A')}[/cyan]\n"
        f"Colors found:  {' '.join(colors[:5]) if colors else 'N/A'}\n\n"
        f"[bold]Final image:[/bold]\n[yellow]{result.get('image_url')}[/yellow]\n\n"
        f"[dim]Raw (no logo):[/dim]\n[dim]{result.get('raw_url')}[/dim]",
        title="Done",
        box=box.ROUNDED
    ))
    console.print("\nOpen in browser or download:")
    console.print(f"  [bold cyan]{result.get('image_url')}[/bold cyan]\n")


# ============================================================
# pocc status --session <id>
# ============================================================

@cli.command()
@click.option("--session", "session_id", required=True)
def status(session_id: str):
    """Check status of a generation job"""
    result = get(f"/jobs/{session_id}/status")
    color = "green" if result["status"] == "done" else "yellow"
    console.print(f"\nSession: [cyan]{session_id}[/cyan]")
    console.print(f"Status:  [{color}]{result['status']}[/{color}]")
    if result.get("error"):
        console.print(f"Error:   [red]{result['error']}[/red]")
    console.print()


# ============================================================
# pocc list
# ============================================================

@cli.command("list")
def list_companies():
    """List all ingested companies"""
    result = get("/companies")
    if not result:
        console.print("[yellow]No companies ingested yet.[/yellow]")
        return

    table = Table(title="Ingested Companies", box=box.ROUNDED)
    table.add_column("Slug", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Industry")
    table.add_column("Products", justify="right")

    for c in result:
        table.add_row(c["slug"], c["name"], c.get("industry", ""), str(c["products"]))

    console.print(table)


if __name__ == "__main__":
    cli()
