"""
Helper script to analyze your Streamlit code with prompt caching.
Reuses cached context for multiple questions about the same codebase.
"""
import anthropic
from pathlib import Path
import sys


def load_project_files():
    """Load key project files that will be cached."""
    project_root = Path(__file__).parent
    files = {}
    
    # Load main files
    for filepath in [
        "app.py",
        "utils/navigation.py",
        "pages/Home.py",
        "pages/Discovery.py",
    ]:
        full_path = project_root / filepath
        if full_path.exists():
            files[filepath] = full_path.read_text()
    
    return files


def analyze_with_cache(question: str):
    """
    Analyze your Streamlit app with prompt caching enabled.
    
    The project code is cached on the first call, then reused on subsequent calls.
    Cost: ~90% discount on cached tokens after first request.
    """
    client = anthropic.Anthropic()
    
    # Load project files once
    project_files = load_project_files()
    
    # Build cached system context
    cached_context = "You are a Streamlit UI/UX expert helping debug and improve the Stock Sentinel app.\n\n"
    cached_context += "## Project Structure\n"
    for filename, content in project_files.items():
        cached_context += f"\n### {filename}\n```python\n{content}\n```\n"
    
    # Make request with cache control
    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=2000,
        system=[
            {
                "type": "text",
                "text": cached_context,
                "cache_control": {"type": "ephemeral"}  # Cache for 5 minutes
            }
        ],
        messages=[
            {"role": "user", "content": question}
        ]
    )
    
    # Show usage (including cache hit/miss)
    usage = response.usage
    cache_creation = getattr(usage, 'cache_creation_input_tokens', 0)
    cache_read = getattr(usage, 'cache_read_input_tokens', 0)
    
    print(f"\n📊 Cache Stats:")
    print(f"   Input tokens: {usage.input_tokens}")
    if cache_creation:
        print(f"   Cache creation: {cache_creation}")
    if cache_read:
        print(f"   Cache read (90% discount): {cache_read}")
    print(f"   Output tokens: {usage.output_tokens}")
    print()
    
    return response.content[0].text


if __name__ == "__main__":
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
    else:
        question = "What's the current structure and styling of the navigation?"
    
    answer = analyze_with_cache(question)
    print(answer)
