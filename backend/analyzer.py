from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Dict, List, Optional

from pydantic import BaseModel


def _patch_ruaccent_token_type_ids() -> None:
    """ruaccent ships ONNX models that require `token_type_ids`, but transformers
    5.x dropped that input from the default tokenizer output. Wrap the affected
    `session.run` calls so a zero `token_type_ids` array is supplied when the
    model expects it. Safe to call repeatedly."""
    import numpy as np
    from ruaccent import accent_model as _am
    from ruaccent import stress_usage_model as _sum

    def _ensure_token_type_ids(session, inputs):
        names = {i.name for i in session.get_inputs()}
        if "token_type_ids" in names and "token_type_ids" not in inputs:
            inputs = dict(inputs)
            inputs["token_type_ids"] = np.zeros_like(inputs["input_ids"])
        return inputs

    if not getattr(_am.AccentModel, "_srl_patched", False):
        orig_put_accent = _am.AccentModel.put_accent

        def put_accent(self, word):
            lower_word = word.lower()
            inputs = self.tokenizer(lower_word, return_tensors="np")
            inputs = {k: v.astype(np.int64) for k, v in inputs.items()}
            inputs = _ensure_token_type_ids(self.session, inputs)
            outputs = self.session.run(None, inputs)
            output_names = {o.name: idx for idx, o in enumerate(self.session.get_outputs())}
            logits = outputs[output_names["logits"]]
            e = np.exp(logits - np.max(logits, axis=-1, keepdims=True))
            probabilities = e / e.sum(axis=-1, keepdims=True)
            scores = np.max(probabilities, axis=-1)[0]
            labels = np.argmax(logits, axis=-1)[0]
            pred_with_scores = [
                {"label": self.id2label[str(label)], "score": float(score)}
                for label, score in zip(labels, scores)
            ]
            return self.render_stress(word, pred_with_scores)

        _am.AccentModel.put_accent = put_accent
        _am.AccentModel._srl_patched = True

    if not getattr(_sum.StressUsagePredictorModel, "_srl_patched", False):
        def predict_stress_usage(self, text):
            inputs = self.tokenizer(
                text,
                return_offsets_mapping=True,
                return_special_tokens_mask=True,
                return_tensors="np",
            )
            offset_mapping = inputs.pop("offset_mapping")[0]
            special_tokens_mask = inputs.pop("special_tokens_mask")[0]
            input_ids = inputs["input_ids"][0]
            inputs = {k: v.astype(np.int64) for k, v in inputs.items()}
            inputs = _ensure_token_type_ids(self.session, inputs)
            outputs = self.session.run(None, inputs)
            logits = outputs[0]
            maxes = np.max(logits, axis=-1, keepdims=True)
            shifted_exp = np.exp(logits - maxes)
            scores = shifted_exp / shifted_exp.sum(axis=-1, keepdims=True)
            pre_entities = self.collect_pre_entities(
                text, input_ids, scores[0], offset_mapping, special_tokens_mask
            )
            return self.aggregate_words(pre_entities, "AVERAGE")

        _sum.StressUsagePredictorModel.predict_stress_usage = predict_stress_usage
        _sum.StressUsagePredictorModel._srl_patched = True

ACUTE = "́"
_PLUS_MARK_RE = re.compile(r"\+([а-яёА-ЯЁ])")

POS_COLOR = {
    "VERB": "verb",
    "AUX": "verb",
    "NOUN": "noun",
    "PROPN": "noun",
    "ADJ": "adj",
    "PRON": "pron",
    "DET": "pron",
    "NUM": "num",
    "ADV": "adv",
}


class Token(BaseModel):
    surface: str
    accented: str
    lemma: str
    pos: str
    morph: Dict[str, str]
    color_class: str
    is_word: bool


class AnalyzedSentence(BaseModel):
    text: str
    tokens: List[Token]


def _strip_accent(s: str) -> str:
    return s.replace(ACUTE, "")


def _split_accented(accented: str) -> List[str]:
    """Naively split ruaccent output into word-like pieces aligned to plain tokens."""
    out: List[str] = []
    buf: List[str] = []
    for ch in accented:
        if ch.isalpha() or ch == ACUTE or ch == "-":
            buf.append(ch)
        else:
            if buf:
                out.append("".join(buf))
                buf = []
            if not ch.isspace():
                out.append(ch)
    if buf:
        out.append("".join(buf))
    return out


class Analyzer:
    def __init__(self) -> None:
        import spacy
        from ruaccent import RUAccent

        _patch_ruaccent_token_type_ids()
        self.nlp = spacy.load("ru_core_news_sm")
        self.accentizer = RUAccent()
        self.accentizer.load(
            omograph_model_size="tiny",
            use_dictionary=True,
            tiny_mode=True,
        )

    def _accent(self, text: str) -> str:
        try:
            raw = self.accentizer.process_all(text)
        except Exception as e:
            print(f"[analyzer] accentizer failed: {type(e).__name__}: {e}", flush=True)
            return text
        # ruaccent tiny_mode marks stress with "+" before the stressed vowel.
        # Convert to U+0301 combining acute after the vowel so the frontend can
        # render it as a real stress mark.
        return _PLUS_MARK_RE.sub(lambda m: m.group(1) + ACUTE, raw)

    def analyze(self, sentence: str) -> AnalyzedSentence:
        return _analyze_cached(self, sentence)

    def analyze_many(self, sentences: List[str]) -> List[AnalyzedSentence]:
        # Use cached results for any sentence we've seen; group the misses and
        # send them through SpaCy as one document so the parser has context
        # across cue boundaries (matters a lot for ASR captions which have no
        # punctuation and no per-sentence sentence boundaries).
        results: List[Optional[AnalyzedSentence]] = [None] * len(sentences)
        missing: List[int] = []
        for i, s in enumerate(sentences):
            cached = _CACHE.get(s)
            if cached is not None:
                results[i] = cached
            else:
                missing.append(i)
        if missing:
            batch = [sentences[i] for i in missing]
            fresh = _analyze_batch_impl(self, batch)
            for k, idx in enumerate(missing):
                _CACHE[sentences[idx]] = fresh[k]
                results[idx] = fresh[k]
        return [r for r in results if r is not None]


# Simple dict cache; we manage eviction ourselves to keep batching consistent.
_CACHE: Dict[str, AnalyzedSentence] = {}
_CACHE_LIMIT = 8192


def _store(text: str, value: AnalyzedSentence) -> None:
    if len(_CACHE) > _CACHE_LIMIT:
        # Drop oldest ~10% (insertion-order). Cheap and good enough.
        drop = len(_CACHE) // 10
        for key in list(_CACHE.keys())[:drop]:
            _CACHE.pop(key, None)
    _CACHE[text] = value


@lru_cache(maxsize=4096)
def _analyze_cached(analyzer: Analyzer, sentence: str) -> AnalyzedSentence:
    return analyzer.analyze_many([sentence])[0]


# Sentinel separator: rare unicode char that SpaCy tokenises as a single PUNCT
# token so we can split the merged document back into per-input chunks.
_SEP = " ‖ "
_SEP_TOKEN = "‖"


def _analyze_batch_impl(analyzer: Analyzer, sentences: List[str]) -> List[AnalyzedSentence]:
    if not sentences:
        return []

    # Run ruaccent per sentence and build a surface→accented map from the union
    # of all outputs. (Joining first would be ~equivalent but per-sentence keeps
    # the accentizer's behaviour predictable.)
    accented_full_list = [analyzer._accent(s) for s in sentences]
    accented_by_surface: Dict[str, str] = {}
    for accented_full in accented_full_list:
        for piece in _split_accented(accented_full):
            plain = _strip_accent(piece)
            if plain and plain not in accented_by_surface:
                accented_by_surface[plain] = piece

    joined = _SEP.join(sentences)
    doc = analyzer.nlp(joined)

    chunks: List[List[Token]] = [[]]
    for tok in doc:
        if tok.text == _SEP_TOKEN:
            chunks.append([])
            continue
        surface = tok.text
        accented = accented_by_surface.get(surface, surface)
        morph: Dict[str, str] = {k: v for k, v in tok.morph.to_dict().items()} if tok.morph else {}
        pos = tok.pos_
        chunks[-1].append(
            Token(
                surface=surface,
                accented=accented,
                lemma=tok.lemma_,
                pos=pos,
                morph=morph,
                color_class=POS_COLOR.get(pos, "other"),
                is_word=tok.is_alpha,
            )
        )

    # Ensure we have exactly one chunk per input. If SpaCy ever merges or splits
    # an unexpected number of separators, fall back to per-sentence analysis.
    if len(chunks) != len(sentences):
        return [_analyze_single(analyzer, s, accented_full_list[i]) for i, s in enumerate(sentences)]

    return [
        AnalyzedSentence(text=accented_full_list[i], tokens=chunks[i])
        for i in range(len(sentences))
    ]


def _analyze_single(analyzer: Analyzer, sentence: str, accented_full: str) -> AnalyzedSentence:
    accented_by_surface: Dict[str, str] = {}
    for piece in _split_accented(accented_full):
        plain = _strip_accent(piece)
        if plain and plain not in accented_by_surface:
            accented_by_surface[plain] = piece
    doc = analyzer.nlp(sentence)
    tokens: List[Token] = []
    for tok in doc:
        surface = tok.text
        accented = accented_by_surface.get(surface, surface)
        morph: Dict[str, str] = {k: v for k, v in tok.morph.to_dict().items()} if tok.morph else {}
        pos = tok.pos_
        tokens.append(
            Token(
                surface=surface,
                accented=accented,
                lemma=tok.lemma_,
                pos=pos,
                morph=morph,
                color_class=POS_COLOR.get(pos, "other"),
                is_word=tok.is_alpha,
            )
        )
    return AnalyzedSentence(text=accented_full, tokens=tokens)
