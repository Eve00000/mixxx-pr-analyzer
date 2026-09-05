import os
import json
import hmac
import hashlib
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

@app.get("/")
def root():
    return {"status": "Mixxx PR Analyzer is running!"}

@app.get("/health")
def health():
    return {"status": "healthy"}

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
        pr_body = data["pull_request"]["body"]
        pr_diff_url = data["pull_request"]["diff_url"]
        
        print(f"📥 PR #{pr_number} in {repo_name}: {pr_title}")
        
        # Get the PR diff
        async with httpx.AsyncClient() as client:
            diff_response = await client.get(pr_diff_url)
            diff_content = diff_response.text
        
        # Analyze with AI
        analysis = await analyze_pr_with_ai(diff_content, pr_body, pr_title)
        
        # Format and print the draft review (will be visible in Render logs)
        print("\n" + "=" * 60)
        print("📝 DRAFT REVIEW COMMENT (copy and paste)")
        print("=" * 60)
        print(analysis)
        print("=" * 60)
        
        return {"status": "PR analyzed"}
    
    return {"status": "ignored"}

async def analyze_pr_with_ai(diff: str, pr_body: str, pr_title: str) -> str:
    """Use Claude to analyze the PR."""
    
    system_prompt = """You are a code review assistant analyzing a pull request for the Mixxx DJ software.
You need to:
1. Detect signs of AI-generated code (patterns like excessive comments, repetitive structures)
2. Evaluate if the contributor understands the code
3. Generate thoughtful questions about the logic and design
4. Assess potential risks and edge cases"""
    
    user_prompt = f"""
Title: {pr_title}
Description: {pr_body[:1000]}
Diff: {diff[:8000]}

Provide your analysis in this format:
1. **Summary**: Brief overview of changes
2. **AI Detection**: Signs of AI generation (if any)
3. **Code Quality**: Observations
4. **Questions**: 3-5 questions to test understanding
5. **Risks**: Potential risks
6. **Recommendation**: Final assessment
"""
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 2000,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}]
            }
        )
        
        if response.status_code == 200:
            result = response.json()
            return result["content"][0]["text"]
        else:
            return f"❌ API Error: {response.status_code}"