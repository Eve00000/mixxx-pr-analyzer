import os
import json
import hmac
import hashlib
from fastapi import FastAPI, Request, HTTPException
import httpx
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Environment variables
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

@app.get("/")
def root():
    return {"status": "Mixxx PR Analyzer is running!"}

@app.get("/health/")
@app.get("/health")
def health():
    return {"status": "healthy", "gemini_configured": GEMINI_API_KEY is not None}

@app.post("/webhook")
async def webhook(request: Request):
    # Get raw payload
    payload = await request.body()
    event = request.headers.get("X-GitHub-Event")
    
    # Parse the data
    data = json.loads(payload)
    
    # Log everything
    print(f"📨 Event: {event}")
    print(f"📦 Full payload: {json.dumps(data, indent=2)[:500]}")  # First 500 chars
    
    # ... rest of your code ...
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

async def analyze_pr_with_gemini_http(diff: str, pr_body: str, pr_title: str, author: str) -> str:
    """Use Google Gemini via direct HTTP API."""
    
    if not GEMINI_API_KEY:
        return "⚠️ Gemini API key not configured. Please add GEMINI_API_KEY to environment variables."
    
    # Limit diff length
    diff_preview = diff[:15000]
    
    # Build the prompt
    prompt_lines = [
        "You are a code review expert analyzing a pull request for the Mixxx DJ software project.",
        "",
        "Pull Request Details:",
        f"- Title: {pr_title}",
        f"- Author: @{author}",
        f"- Description: {pr_body[:1000]}",
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
        "2. AI Detection: Does this code show signs of being AI-generated? Look for:",
        "   - Excessive or robotic comments",
        "   - Unnatural variable/function names",
        "   - Repetitive patterns that don't match project style",
        "   - Code that doesn't fit the codebase architecture",
        "   - Overly verbose or textbook-style implementations",
        "   Rate as: HIGH / MEDIUM / LOW / NONE and explain why.",
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
        "",
        "5. Risks & Edge Cases: What potential issues should a reviewer look for?",
        "",
        "6. Recommendation: Should this PR be merged, need changes, or needs more discussion?",
        "",
        "Keep your response concise but thorough. Write in a professional but approachable tone."
    ]
    
    prompt = "\n".join(prompt_lines)
    
    # Gemini API endpoint
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    
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
        "- [ ] Verify the AI detection assessment",
        "- [ ] Check that the questions are relevant",
        "- [ ] Add any personal observations",
        "- [ ] Decide: APPROVE / REQUEST CHANGES / COMMENT",
        "",
        "### 🔗 Quick Links:",
        "- [View PR](https://github.com/mixxxdj/mixxx/pulls)",
        "- [Mixxx Contributing Guide](https://github.com/mixxxdj/mixxx/blob/main/CONTRIBUTING.md)"
    ]
    
    return "\n".join(lines)