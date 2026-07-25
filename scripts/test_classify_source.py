"""Tests for intake/classify_source.py (pure rules, no network)."""
import pytest

from scripts.intake import classify_source as cs


@pytest.mark.parametrize(
    "text,expected_conf,expected_subtype,desc",
    [
        ("https://arxiv.org/abs/2605.26112", "high", "arxiv_paper", "arXiv"),
        ("https://arxiv.org/pdf/2501.12345", "high", "arxiv_paper", "arXiv PDF"),
        ("https://github.com/user/repo", "high", "github", "GitHub"),
        ("https://mp.weixin.qq.com/s/abc123", "high", "weixin", "WeChat"),
        ("https://www.linkedin.com/my-items/saved-posts/", "high", "linkedin", "LinkedIn"),
        ("https://huggingface.co/meta-llama/Llama-3", "high", "huggingface", "HF"),
        ("https://twitter.com/user/status/123", "high", "twitter_x", "Twitter"),
        ("https://x.com/user/status/123", "high", "twitter_x", "X.com"),
        ("https://www.youtube.com/watch?v=abc", "high", "youtube", "YouTube"),
        ("https://youtu.be/abc", "high", "youtube", "YouTube short"),
        ("https://www.bilibili.com/video/BV123", "high", "bilibili", "B站"),
        ("https://medium.com/@user/article", "high", "tech_blog", "Medium"),
        ("https://myproject.github.io/docs", "high", "project_page", "GitHub Pages"),
        ("https://docs.python.org/3/library", "high", "docs", "Docs site"),
        ("https://blog.example.com/post", "high", "tech_blog", "Blog domain"),
        ("https://some-random-site.com/page", "low", "generic_web", "Unknown domain"),
        ("解读 2605.26112", "high", "arxiv_id", "arXiv ID only"),
        ("看看这篇 2501.12345v1", "high", "arxiv_id", "arXiv ID with version"),
        ("解读 OpenAI 这家公司", "medium", "startup_name", "Company by keywords"),
        ("VLA 是什么", "medium", "concept_query", "Concept query"),
        ("对比 GPT-5 和 Gemini 3", "medium", "comparison", "Comparison"),
        ("2026年VLA发展趋势", "medium", "trend_question", "Trend question"),
        ("解读 Yann LeCun 的研究方向", "medium", "researcher", "Researcher by name"),
        ("解读这个开源项目", "medium", "project_name", "Project analysis"),
    ],
)
def test_classify_cases(text, expected_conf, expected_subtype, desc):
    result = cs.classify(text)
    assert result["status"] in ("ok", "ambiguous"), f"{desc}: unexpected status {result['status']}"
    found = any(
        r.get("subtype") == expected_subtype and r.get("confidence") == expected_conf
        for r in result["results"]
    )
    assert found, f"{desc}: expected ({expected_subtype},{expected_conf}), got {result['results']}"


def test_classify_empty():
    result = cs.classify("")
    assert result["status"] == "unclassifiable"
    assert result["results"] == []


def test_classify_multiple_urls():
    result = cs.classify(
        "https://arxiv.org/abs/2605.26112 和 https://github.com/user/repo"
    )
    subtypes = {r.get("subtype") for r in result["results"]}
    assert "arxiv_paper" in subtypes
    assert "github" in subtypes


def test_extract_urls():
    text = "See https://a.com and https://b.com/path"
    urls = cs.extract_urls(text)
    assert set(urls) == {"https://a.com", "https://b.com/path"}


def test_classify_url():
    result = cs.classify_url("https://arxiv.org/abs/2605.26112")
    assert result["subtype"] == "arxiv_paper"
    assert result["confidence"] == "high"
