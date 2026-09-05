import os
import json
import hmac
import hashlib
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
import httpx
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Environment variables
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # For public API rate limits

@app.get("/")
def root():
    return {"status": "Mixxx PR Analyzer is running!"}

@app.get("/health/")
@app.get("/health")
def health():
    return {"status": "healthy", "gemini_configured": GEMINI_API_KEY is not None}

@app.post("/webhook")
async def webhook(request: Request):
    """Handle GitHub webhook events for PRs in your fork."""
    
    payload = await request.body()
    event = request.headers.get("X-GitHub-Event")
    signature = request.headers.get("X-Hub-Signature-256")
    
    # Debug: log the raw event
    print(f"📨 Received event: {event}")
    data = json.loads(payload)
    print(f"📦 Action: {data.get('action')}")
    print(f"📦 Repository: {data.get('repository', {}).get('full_name')}")
    
    # Verify webhook signature
    if GITHUB_WEBHOOK_SECRET:
        expected = "sha256=" + hmac.new(
            GITHUB_WEBHOOK_SECRET.encode(),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=401, detail="Invalid signature")
    
    data = json.loads(payload)
    
    # Only handle pull request events
    if event == "pull_request" and data.get("action") in ["opened", "synchronize"]:
        repo_name = data["repository"]["full_name"]
        pr_number = data["pull_request"]["number"]
        pr_title = data["pull_request"]["title"]
        pr_body = data["pull_request"]["body"] or ""
        pr_diff_url = data["pull_request"]["diff_url"]
        pr_author = data["pull_request"]["user"]["login"]
        
        print(f"📥 PR #{pr_number} in {repo_name} by @{pr_author}: {pr_title}")
        print("-" * 60)
        
        # Get the PR diff
        async with httpx.AsyncClient() as client:
            diff_response = await client.get(pr_diff_url)
            diff_content = diff_response.text
        
        # Analyze with Gemini via direct HTTP API
        analysis = await analyze_pr_with_gemini_http(diff_content, pr_body, pr_title, pr_author)
        
        # Format the draft review
        draft_review = format_review_comment(analysis, pr_author)
        
        # Print to logs (you'll copy from Render logs)
        print("\n" + "=" * 70)
        print("📝 DRAFT REVIEW (Copy and paste this into the PR)")
        print("=" * 70)
        print(draft_review)
        print("=" * 70)
        
        return {"status": "PR analyzed"}
    
    return {"status": "ignored"}

@app.get("/analyze-public-pr")
async def analyze_public_pr(pr_number: int):
    """
    Manually analyze a specific PR in the public Mixxx repository.
    Returns plain text for easy reading.
    Usage: https://mixxx-pr-analyzer.onrender.com/analyze-public-pr?pr_number=12345
    """
    
    if not GEMINI_API_KEY:
        return PlainTextResponse("Error: Gemini API key not configured")
    
    try:
        print(f"🔍 Manually analyzing PR #{pr_number} from public Mixxx repo")
        print("-" * 60)
        
        # Prepare headers with token if available
        headers = {}
        if GITHUB_TOKEN:
            headers["Authorization"] = f"token {GITHUB_TOKEN}"
        
        # Get PR details from public GitHub API
        async with httpx.AsyncClient() as client:
            pr_response = await client.get(
                f"https://api.github.com/repos/mixxxdj/mixxx/pulls/{pr_number}",
                headers=headers
            )
            
            if pr_response.status_code != 200:
                return PlainTextResponse(
                    f"Error: Failed to fetch PR #{pr_number}\n"
                    f"Status: {pr_response.status_code}\n"
                    f"Message: {pr_response.text[:200]}"
                )
            
            pr_data = pr_response.json()
            
            # Get PR diff
            diff_response = await client.get(pr_data["diff_url"])
            diff_content = diff_response.text
        
        # Extract PR details
        pr_title = pr_data["title"]
        pr_body = pr_data.get("body", "")
        pr_author = pr_data["user"]["login"]
        pr_html_url = pr_data["html_url"]
        pr_state = pr_data["state"]
        pr_created = pr_data["created_at"]
        pr_updated = pr_data["updated_at"]
        
        print(f"📥 PR #{pr_number}: {pr_title} by @{pr_author}")
        print(f"🔗 {pr_html_url}")
        print("-" * 60)
        
        # Analyze with Gemini
        analysis = await analyze_pr_with_gemini_http(
            diff_content,
            pr_body,
            pr_title,
            pr_author
        )
        
        # Format the draft review
        draft_review = format_review_comment(analysis, pr_author)
        
        # Print to logs
        print("\n" + "=" * 70)
        print("📝 DRAFT REVIEW (Copy and paste this into the PR)")
        print("=" * 70)
        print(draft_review)
        print("=" * 70)
        
        # Return human-readable plain text
        header = f"""
╔══════════════════════════════════════════════════════════════════════╗
║                    📋 PR ANALYSIS REPORT                            ║
╚══════════════════════════════════════════════════════════════════════╝

  PR #{pr_number}: {pr_title}
  Author: @{pr_author}
  State: {pr_state}
  Created: {pr_created}
  Updated: {pr_updated}
  URL: {pr_html_url}

════════════════════════════════════════════════════════════════════════

{draft_review}

════════════════════════════════════════════════════════════════════════
  💡 Copy the review above, edit it, and post it on the PR.
  🔗 {pr_html_url}
════════════════════════════════════════════════════════════════════════
"""
        
        return PlainTextResponse(content=header, media_type="text/plain")
        
    except Exception as e:
        error_msg = f"Error analyzing PR #{pr_number}: {str(e)}"
        print(f"❌ {error_msg}")
        return PlainTextResponse(f"Error: {error_msg}")

def check_for_ai_disclosure(text: str) -> str:
    """Check if the PR description explicitly mentions AI assistance."""
    disclosure_phrases = [
        "written with the help of ai",
        "written with ai",
        "ai-generated",
        "claude",
        "chatgpt",
        "copilot",
        "gemini",
        "ai-assisted",
        "with the help of ai",
        "generated by ai",
        "created with ai",
        "using ai",
        "ai wrote",
        "help of ai",
        "written by ai",
        "ai helped",
        "ai assistance",
    ]
    
    text_lower = text.lower()
    for phrase in disclosure_phrases:
        if phrase in text_lower:
            return f"⚠️ DISCLOSED - contains '{phrase}'"
    return None

async def analyze_pr_with_gemini_http(diff: str, pr_body: str, pr_title: str, author: str) -> str:
    """Use Google Gemini via direct HTTP API."""
    
    if not GEMINI_API_KEY:
        return "⚠️ Gemini API key not configured. Please add GEMINI_API_KEY to environment variables."
    
    # PRE-SCAN for AI disclosure
    disclosure_check = check_for_ai_disclosure(pr_body)
    disclosure_note = ""
    if disclosure_check:
        disclosure_note = f"\n\n**⚠️ IMPORTANT: The PR description explicitly mentions AI assistance: {disclosure_check}**"
        print(f"📢 AI Disclosure detected in PR: {disclosure_check}")
    
    # Limit diff length
    diff_preview = diff[:15000]
    
    # Build the prompt
    prompt_lines = [
        "You are a code review expert analyzing a pull request for the Mixxx DJ software project.",
        "",
        "Pull Request Details:",
        f"- Title: {pr_title}",
        f"- Author: @{author}",
        f"- Description: {pr_body[:1000]}{disclosure_note}",
        "",
        "Code Changes (Diff):",
        "```",
        diff_preview,
        "```",
        "",
        "Please provide a thorough code review focusing on:",
        "",
        "1. Summary: A brief overview of what this PR does (1-2 sentences).",
        "",
        "2. AI Detection & Disclosure:",
        "   The description has been pre-scanned for AI disclosure. The result is shown above.",
        "   If disclosure was found, rate as 'DISCLOSED - AI-Assisted' and focus on code quality.",
        "   If no disclosure was found, look for stylistic signs of AI generation.",
        "   Rate as: DISCLOSED / HIGH / MEDIUM / LOW / NONE",
        "   Explain your reasoning.",
        "",
        "3. Code Quality: Evaluate the code quality:",
        "   - Does it follow Mixxx's coding standards?",
        "   - Are there any obvious bugs or logic errors?",
        "   - Is the code maintainable?",
        "",
        "4. Understanding Questions: Generate 3-5 specific questions to ask the contributor to verify they understand their own code. These should be about:",
        "   - Why specific design decisions were made",
        "   - Edge cases they might not have considered",
        "   - How their code interacts with existing Mixxx features",
        "   - Any trade-offs or alternatives they considered",
        "",
        "5. Risks & Edge Cases: What potential issues should a reviewer look for?",
        "",
        "6. Recommendation: Should this PR be merged, need changes, or needs more discussion?",
        "",
        "Keep your response concise but thorough. Write in a professional but approachable tone."
    ]
    
    prompt = "\n".join(prompt_lines)
    
    # Gemini API endpoint - using recommended model
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_API_KEY}"
    
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(url, json=payload)
            
            if response.status_code == 200:
                result = response.json()
                
                # Extract the text from the response
                try:
                    text = result["candidates"][0]["content"]["parts"][0]["text"]
                    return text
                except (KeyError, IndexError):
                    print(f"❌ Unexpected response structure: {json.dumps(result, indent=2)}")
                    return "⚠️ Error parsing Gemini response. Please try again or review manually."
            else:
                error_msg = f"Gemini API error: {response.status_code} - {response.text}"
                print(f"❌ {error_msg}")
                return f"⚠️ {error_msg}"
                
        except httpx.TimeoutException:
            return "⚠️ Gemini API timeout. The PR diff might be too large. Please review manually."
        except Exception as e:
            error_msg = f"Error calling Gemini: {str(e)}"
            print(f"❌ {error_msg}")
            return f"⚠️ {error_msg}"

def format_review_comment(analysis: str, author: str) -> str:
    """Format the analysis as a GitHub review comment."""
    
    lines = [
        "## 🤖 AI-Assisted Code Review (Draft)",
        "",
        "> **Note from maintainer:** This is an automated analysis using Google Gemini. Please edit this review before posting. Remove, add, or modify any part as you see fit.",
        "",
        "---",
        "",
        analysis,
        "",
        "---",
        "",
        "### 📋 Maintainer Checklist Before Posting:",
        "- [ ] Verify the AI detection assessment (especially if AI was disclosed)",
        "- [ ] Check that the questions are relevant",
        "- [ ] Add any personal observations",
        "- [ ] Decide: APPROVE / REQUEST CHANGES / COMMENT",
        "",
        "### 🔗 Quick Links:",
        "- [View PR](https://github.com/mixxxdj/mixxx/pulls)",
        "- [Mixxx Contributing Guide](https://github.com/mixxxdj/mixxx/blob/main/CONTRIBUTING.md)"
    ]
    
    return "\n".join(lines)