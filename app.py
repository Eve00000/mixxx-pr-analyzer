import os
import json
import hmac
import hashlib
from fastapi import FastAPI, Request, HTTPException
import httpx
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

app = FastAPI()

# Environment variables
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Configure Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')  # Free tier model
else:
    model = None

@app.get("/")
def root():
    return {"status": "Mixxx PR Analyzer is running!"}

@app.get("/health")
def health():
    return {"status": "healthy", "gemini_configured": model is not None}

@app.post("/webhook")
async def webhook(request: Request):
    """Main webhook endpoint for GitHub events."""
    
    payload = await request.body()
    event = request.headers.get("X-GitHub-Event")
    signature = request.headers.get("X-Hub-Signature-256")
    
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
        
        # Analyze with Gemini
        analysis = await analyze_pr_with_gemini(diff_content, pr_body, pr_title, pr_author)
        
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

async def analyze_pr_with_gemini(diff: str, pr_body: str, pr_title: str, author: str) -> str:
    """Use Google Gemini to analyze the PR."""
    
    if not model:
        return "⚠️ Gemini API key not configured. Please add GEMINI_API_KEY to environment variables."
    
    # Limit diff length to avoid token limits (Gemini Flash has generous limits)
    diff_preview = diff[:15000]  # Gemini Flash can handle ~1M tokens, but we'll be safe
    
    prompt = f"""
You are a code review expert analyzing a pull request for the Mixxx DJ software project.

**Pull Request Details:**
- Title: {pr_title}
- Author: @{author}
- Description: {pr_body[:1000]}

**Code Changes (Diff):**