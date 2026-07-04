# Example Run: Step-by-Step Guide

This guide walks you through a complete example run of the **POCC (Product Creative Platform)** pipeline. We will use a mock company **"Spicen Foods"** and the campaign topic **"summer pool party"** to demonstrate how to ingest a brand, brainstorm campaigns, customize image prompts, and generate a final branded product shot.

---

## 🛠️ Step 0: Start the Backend Server

Before running any CLI commands, make sure the FastAPI server is running in the background.

1. Open your terminal.
2. Navigate to your project folder:
   ```bash
   cd "c:\Users\Upendra Ravuri\Desktop\image-improv"
   ```
3. Activate the virtual environment:
   ```bash
   # On Windows (PowerShell):
   .\venv\Scripts\Activate.ps1
   
   # On Windows (CMD):
   .\venv\Scripts\activate.bat
   
   # On Linux/macOS:
   source venv/bin/activate
   ```
4. Start the server:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```
   *Keep this terminal window open. We will run the commands below in a **new** terminal window.*

---

## 🌐 Step 1: Ingest the Company Website

We will feed the pipeline a URL. The system will automatically scrape the website, extract products, detect brand voices, and download logos/images.

Open a **new terminal**, activate the virtual environment, and run:

```bash
python cli/pocc.py ingest --url https://spicenfood.com
```

### What happens:
*   Scrapes the website.
*   Finds available products (e.g., *Mango Margarita Frost*).
*   Downloads the company logo and computes brand colors.
*   Assigns a unique reference slug (e.g., `spicen_foods`).

**Example Terminal Output:**
```text
Company saved.

Name:      Spicen Foods
Industry:  Food & Beverage
Products:  5 found
Logo:      saved (local path: ./data/images/logos/spicen_foods_logo.png)
Colors:    primary: #FF5733 | secondary: #33CCFF

Reference this company as: spicen_foods
```

---

## 💡 Step 2: Generate Campaign Themes

Now, let's ask the LLM to brainstorm 5 distinct campaign concepts matching a topic (e.g., `"summer pool party"`).

```bash
python cli/pocc.py campaign --company spicen_foods --topic "summer pool party"
```

**Example Terminal Output:**
```text
Campaign Themes — summer pool party
┌───┬─────────────────────────┬────────────────────────────────────────────┬────────────────────────┬────────────┐
│ # │ Theme                   │ Concept                                    │ Product                │ Mood       │
├───┼─────────────────────────┼────────────────────────────────────────────┼────────────────────────┼────────────┤
│ 1 │ Backyard Freedom Party  │ Celebrate with frozen cocktails & friends  │ Mango Margarita Frost  │ energetic  │
│ 2 │ Twilight Oasis Lounge   │ Relax by the pool as day turns to night    │ Classic Lime Margarita │ serene     │
│ 3 │ ...                     │ ...                                        │ ...                    │ ...        │
└───┴─────────────────────────┴────────────────────────────────────────────┴────────────────────────┴────────────┘

Session ID: 47d4d870-c703-45ad-9028-8b00dd7c2559
```
*Take note of the **Session ID** printed at the bottom! You will need it for the next steps.*

---

## 🎯 Step 3: Select a Theme & Generate Image Ideas

Let's pick **Theme 1** (Backyard Freedom Party). The system will automatically match it with the *Mango Margarita Frost* product and generate 3 background scene ideas with varying intensities of theme presence.

```bash
python cli/pocc.py select --session 47d4d870-c703-45ad-9028-8b00dd7c2559 --theme 1
```

**Example Terminal Output:**
```text
Selected Theme: Backyard Freedom Party
Product: Mango Margarita Frost

Image Ideas:
┌───┬────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ # │ Scene Idea                                                                                                     │
├───┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 1 │ [THEME-RICH] Dappled afternoon sunlight reflecting pool water onto a wooden deck with a sunglasses prop nearby. │
│ 2 │ [SIMPLIFIED] Soft turquoise water reflection on a clean light-colored concrete surface.                        │
│ 3 │ [ULTRA MINIMAL] Near-monochromatic matte teal backdrop under a single soft directional spotlight.               │
└───┴────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

Next: pocc image --session 47d4d870-c703-45ad-9028-8b00dd7c2559 --idea <number>
The image command will preview and let you edit the Flux prompt before generating.
```

---

## 🎨 Step 4: Preview, Tweak, and Generate the Final Image

You can trigger the entire preview and generation flow directly from the CLI or selectively using the API.

### Option A: The CLI Method (Interactive & Quick)

Run the `pocc image` command specifying your session ID and chosen idea number:

```bash
python cli/pocc.py image --session 47d4d870-c703-45ad-9028-8b00dd7c2559 --idea 1
```

#### What happens:
1. **Interactive Preview:** The CLI builds the compressed Flux prompt using the LLM and shows it in a clean preview panel.
2. **Inline Tweaking:** It asks you: `Edit this prompt? (y/N)`
   * If you choose `y`, you can type your aesthetic adjustment (e.g. `"Make it golden hour lighting, warmer tones"`).
   * The backend will regenerate the prompt and display the updated preview.
3. **Flux Generation:** Once you confirm (`N` or press Enter), it submits the prompt to Flux 2 Pro, runs the polling bar, checks corner contrast, pastes the logo, and outputs the final image URL.

**CLI Interaction Example:**
```text
Step 1/2 — Building Flux prompt for idea 1…

┌─────────────────────────────── Flux Prompt Preview ────────────────────────────────┐
│ Scene: Dappled afternoon sunlight reflecting pool water onto a wooden deck.        │
│                                                                                    │
│ PRESERVE EXACTLY: Product shape, packaging, label text, and scale.                 │
│ CHANGE ONLY: Dappled sunlight reflecting pool water onto a wooden deck.            │
│ REALISM: DSLR product photography, natural highlights, soft shadow falloff, no CGI │
└────────────────────────────────────────────────────────────────────────────────────┘

Edit this prompt? (y/N)  y
  Describe your tweak
  e.g. "Make it golden hour lighting, warmer tones"
  › Make it golden hour lighting, warmer tones

Regenerating prompt with your tweak…

┌─────────────────────────────── Flux Prompt Preview ────────────────────────────────┐
│ Scene: Dappled afternoon sunlight reflecting pool water onto a wooden deck.        │
│                                                                                    │
│ PRESERVE EXACTLY: Product shape, packaging, label text, and scale.                 │
│ CHANGE ONLY: Warm golden hour sunlight reflecting pool water onto a wooden deck.   │
│ REALISM: DSLR product photography, natural highlights, soft shadow falloff, no CGI │
│                                                                                    │
│ Applied tweak: Make it golden hour lighting, warmer tones                          │
└────────────────────────────────────────────────────────────────────────────────────┘

Edit this prompt? (y/N)  n

Step 2/2 — Generating image with Flux 2 Pro…
⠹ Submitting to Flux…
```

---

### Option B: The API Method (Manual / UI Integration)

If you prefer to bypass the CLI and integrate with a custom frontend, you can use the backend `/docs` page:

1. **Preview the Prompt:** Call `POST /api/v1/preview-prompt` with your `session_id` and `idea_number`.
2. **Submit for Generation:** Call `POST /api/v1/generate-from-prompt` passing your confirmed `flux_prompt` inside the request body.

---

### Final Verification

On successful completion, you will receive the final download link:

```text
Image generated.

Idea used:     Dappled afternoon sunlight reflecting pool water onto a wooden deck.
Logo placed:   bottom_right
Colors found:  #FFA500 #008080 #FFFFFF

Final image:
http://localhost:8000/api/v1/jobs/47d4d870-c703-45ad-9028-8b00dd7c2559/image
```

Open the link in your browser to inspect the finalized branded creative.
