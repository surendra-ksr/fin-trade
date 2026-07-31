"""Sentiment engine: FinBERT with deterministic lexicon fallback.

The engine tries to load a pre-trained FinBERT model from `transformers`.
When weights are unavailable (offline mode), it falls back to a deterministic
lexicon-based score computed from positive/negative word lists. All tests
run offline: the model is mocked, but the lexicon path is fully exercised.

Scores are persisted back to `news_events.sentiment_score`.
"""
from __future__ import annotations
import logging
from typing import Optional, List
import numpy as np
import pandas as pd

from data.database import get_database
from utils.logger import get_logger

_log = get_logger("models.sentiment")

# Deterministic lexicon (used when FinBERT weights unavailable)
_POSITIVE_WORDS = {
    "good", "great", "excellent", "strong", "growth", "profit", "gain", "up",
    "positive", "surge", "boost", "improvement", "success", "bullish",
    "upgrade", "beat", "exceed", "record", "positive", "favorable",
}
_NEGATIVE_WORDS = {
    "bad", "poor", "weak", "loss", "decline", "fall", "down", "negative",
    "crash", "drop", "miss", "failure", "bearish", "downgrade", "cut",
    "warning", "concern", "risk", "uncertainty", "deficit", "bankrupt",
}


def _lexicon_score(text: str) -> float:
    """Deterministic lexicon-based sentiment score in [-1, 1]."""
    tokens = set(text.lower().split())
    pos = sum(1 for w in tokens if w in _POSITIVE_WORDS)
    neg = sum(1 for w in tokens if w in _NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0
    return float(pos - neg) / float(total)


class SentimentEngine:
    """News sentiment scorer with FinBERT + lexicon fallback."""

    version = "5.1-sentiment"

    def __init__(
        self,
        model_name: str = "ProsusAI/finbert",
        use_lexicon_fallback: bool = True,
        seed: Optional[int] = None,
    ) -> None:
        self.model_name = model_name
        self.use_lexicon_fallback = bool(use_lexicon_fallback)
        self.seed = seed
        self._model = None
        self._tokenizer = None
        self._loaded = False
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            # Only load if weights are accessible; otherwise rely on fallback
            self._tokenizer = AutoTokenizer.from_pretrained(model_name)
            self._model = AutoModelForSequenceClassification.from_pretrained(model_name)
            self._loaded = True
            _log.info("FinBERT model loaded: %s", model_name)
        except Exception as exc:
            if self.use_lexicon_fallback:
                _log.info("FinBERT unavailable (%s); using lexicon fallback", exc)
            else:
                _log.warning("FinBERT unavailable and fallback disabled: %s", exc)

    def score_text(self, text: str) -> float:
        """Score a single text string; return float in [0, 1] (positive = bullish)."""
        if self._loaded and self._model is not None and self._tokenizer is not None:
            try:
                import torch
                inputs = self._tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
                with torch.no_grad():
                    outputs = self._model(**inputs)
                    probs = torch.softmax(outputs.logits, dim=-1)
                    # For FinBERT, labels are typically [negative, neutral, positive]
                    # We return the positive probability
                    if probs.shape[-1] >= 2:
                        return float(probs[0][-1].item())
                    else:
                        return float(probs[0][0].item())
            except Exception:
                # Fall through to lexicon on any error
                pass
        # Lexicon fallback (deterministic, offline-safe)
        lex = _lexicon_score(text)
        # Map [-1, 1] to [0, 1]
        return float((lex + 1.0) / 2.0)

    def score_news_row(self, headline: str, content: Optional[str] = None) -> float:
        """Score a news item from headline (+ optional content)."""
        combined = str(headline) + (" " + (str(content) if content is not None else ""))
        return self.score_text(combined)

    def process_batch(
        self,
        news_df: pd.DataFrame,
        *,
        persist: bool = True,
        symbol_col: str = "symbol",
    ) -> pd.DataFrame:
        """Process all news rows and optionally persist scores to DB."""
        db = get_database()
        results = []
        for _, row in news_df.iterrows():
            headline = str(row.get("headline", ""))
            content = row.get("content")
            symbol = str(row.get(symbol_col, "")).upper()
            score = self.score_news_row(headline, content)
            results.append({"symbol": symbol, "headline": headline, "score": score})
            if persist:
                db.upsert_sentiment(
                    symbol=symbol,
                    date_value=row.get("published_at", pd.Timestamp.utcnow()),
                    source="finbert_lexicon_fallback",
                    score=score,
                    payload={"headline": headline, "content": content},
                )
        return pd.DataFrame(results)
