from __future__ import annotations

import re
import threading
from typing import Callable, Dict, List, Optional, Tuple

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
# Apostrophes are word-internal in Ukrainian (ім'я, п'ять) and spaCy keeps such
# words as one token, so the splitter has to as well.
_APOSTROPHES = ("'", "’")
# A "word" for colouring/tooltip purposes: letters, optionally joined by
# internal apostrophes. `tok.is_alpha` is False for п'ять, which is why we
# don't use it.
_WORD_RE = re.compile(r"^[^\W\d_]+(?:['’][^\W\d_]+)*$", re.UNICODE)

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

SUPPORTED_LANGS = ("ru", "uk")


class Token(BaseModel):
    surface: str
    accented: str
    lemma: str
    pos: str
    morph: Dict[str, str]
    color_class: str
    is_word: bool
    # True when the tokenizer saw whitespace after this token. The frontend
    # needs it to re-join tokens: no punctuation heuristic gets кто-то right.
    ws: bool


class AnalyzedSentence(BaseModel):
    text: str
    tokens: List[Token]


def _strip_accent(s: str) -> str:
    return s.replace(ACUTE, "")


def _split_accented(accented: str) -> List[str]:
    """Split accentizer output into word-like pieces aligned to spaCy's tokens.

    Alphanumerics and the combining acute accumulate into one piece, so digit
    runs stay whole. An apostrophe joins the piece only between two letters.
    Everything else, hyphens included, becomes its own piece — spaCy splits
    кто-то into three tokens, so keeping the hyphen glued here would make
    every hyphenated word miss the lookup.
    """
    out: List[str] = []
    buf: List[str] = []
    for i, ch in enumerate(accented):
        if ch.isalnum() or ch == ACUTE:
            buf.append(ch)
            continue
        if (
            ch in _APOSTROPHES
            and _strip_accent("".join(buf))[-1:].isalpha()
            and accented[i + 1 : i + 2].isalpha()
        ):
            buf.append(ch)
            continue
        if buf:
            out.append("".join(buf))
            buf = []
        if not ch.isspace():
            out.append(ch)
    if buf:
        out.append("".join(buf))
    return out


class _AccentCursor:
    """Feeds accentizer pieces to spaCy tokens for ONE sentence.

    Positional first: pieces are consumed in order, so a homograph that occurs
    twice with different stress (за́мок / замо́к) gets the right one each time. A
    surface map is kept only as a fallback for when the two tokenizers disagree
    about how to cut the text.
    """

    _WINDOW = 4

    def __init__(self, accented_full: str) -> None:
        self.pieces = _split_accented(accented_full)
        self.plain = [_strip_accent(p) for p in self.pieces]
        self.pos = 0
        self.by_surface: Dict[str, str] = {}
        for piece, plain in zip(self.pieces, self.plain):
            if plain and plain not in self.by_surface:
                self.by_surface[plain] = piece

    def take(self, surface: str) -> str:
        # Scan a short window so one token the two tokenizers cut differently
        # doesn't desynchronise the rest of the sentence.
        for j in range(self.pos, min(self.pos + self._WINDOW, len(self.pieces))):
            if self.plain[j] == surface:
                self.pos = j + 1
                return self.pieces[j]
        return self.by_surface.get(surface, surface)


class _LangPipeline:
    def __init__(self, nlp, accent: Callable[[str], str]) -> None:
        self.nlp = nlp
        self.accent = accent


def _build_ru_pipeline() -> _LangPipeline:
    import spacy
    from ruaccent import RUAccent

    _patch_ruaccent_token_type_ids()
    nlp = spacy.load("ru_core_news_sm")
    accentizer = RUAccent()
    accentizer.load(
        omograph_model_size="tiny",
        use_dictionary=True,
        tiny_mode=True,
    )

    def accent(text: str) -> str:
        try:
            raw = accentizer.process_all(text)
        except Exception as e:
            print(f"[analyzer ru] accentizer failed: {type(e).__name__}: {e}", flush=True)
            return text
        # ruaccent tiny_mode marks stress with "+" before the stressed vowel.
        # Convert to U+0301 combining acute after the vowel so the frontend can
        # render it as a real stress mark.
        return _PLUS_MARK_RE.sub(lambda m: m.group(1) + ACUTE, raw)

    return _LangPipeline(nlp, accent)


def _build_uk_pipeline() -> _LangPipeline:
    import spacy
    from ukrainian_word_stress import Stressifier

    nlp = spacy.load("uk_core_news_sm")
    stressify = Stressifier(stress_symbol=ACUTE, on_ambiguity="all")

    def accent(text: str) -> str:
        try:
            return stressify(text)
        except Exception as e:
            print(f"[analyzer uk] stressifier failed: {type(e).__name__}: {e}", flush=True)
            return text

    return _LangPipeline(nlp, accent)


_PIPELINE_BUILDERS: Dict[str, Callable[[], _LangPipeline]] = {
    "ru": _build_ru_pipeline,
    "uk": _build_uk_pipeline,
}


class Analyzer:
    def __init__(self) -> None:
        self._pipelines: Dict[str, _LangPipeline] = {}
        self._build_lock = threading.Lock()

    def _pipeline(self, lang: str) -> _LangPipeline:
        if lang not in _PIPELINE_BUILDERS:
            raise ValueError(f"unsupported source language: {lang}")
        cached = self._pipelines.get(lang)
        if cached is not None:
            return cached
        # FastAPI runs these sync endpoints in a thread pool and the extension
        # fires analyze + prefetch concurrently, so without the lock two
        # threads both miss and both load the (very heavy) models.
        with self._build_lock:
            if lang not in self._pipelines:
                self._pipelines[lang] = _PIPELINE_BUILDERS[lang]()
            return self._pipelines[lang]

    def warm(self, lang: str) -> None:
        self._pipeline(lang)

    def analyze(self, sentence: str, lang: str = "ru") -> AnalyzedSentence:
        return self.analyze_many([sentence], lang)[0]

    def analyze_many(self, sentences: List[str], lang: str = "ru") -> List[AnalyzedSentence]:
        # Use cached results for any sentence we've seen; group the misses and
        # send them through SpaCy as one document so the parser has context
        # across cue boundaries (matters a lot for ASR captions which have no
        # punctuation and no per-sentence sentence boundaries).
        results: List[Optional[AnalyzedSentence]] = [None] * len(sentences)
        missing: List[int] = []
        for i, s in enumerate(sentences):
            cached = _CACHE.get((lang, s))
            if cached is not None:
                results[i] = cached
            else:
                missing.append(i)
        if missing:
            batch = [sentences[i] for i in missing]
            fresh = _analyze_batch_impl(self._pipeline(lang), batch)
            for k, idx in enumerate(missing):
                _store((lang, sentences[idx]), fresh[k])
                results[idx] = fresh[k]
        return [r for r in results if r is not None]


# Simple dict cache; we manage eviction ourselves to keep batching consistent.
_CacheKey = Tuple[str, str]
_CACHE: Dict[_CacheKey, AnalyzedSentence] = {}
_CACHE_LIMIT = 8192


def _store(key: _CacheKey, value: AnalyzedSentence) -> None:
    if len(_CACHE) > _CACHE_LIMIT:
        # Drop oldest ~10% (insertion-order). Cheap and good enough.
        drop = len(_CACHE) // 10
        for k in list(_CACHE.keys())[:drop]:
            _CACHE.pop(k, None)
    _CACHE[key] = value


# Sentinel separator: rare unicode char that SpaCy tokenises as a single PUNCT
# token so we can split the merged document back into per-input chunks.
_SEP = " ‖ "
_SEP_TOKEN = "‖"


def _make_token(tok, cursor: Optional[_AccentCursor]) -> Token:
    surface = tok.text
    morph: Dict[str, str] = {k: v for k, v in tok.morph.to_dict().items()} if tok.morph else {}
    pos = tok.pos_
    return Token(
        surface=surface,
        accented=cursor.take(surface) if cursor is not None else surface,
        lemma=tok.lemma_,
        pos=pos,
        morph=morph,
        color_class=POS_COLOR.get(pos, "other"),
        is_word=bool(_WORD_RE.match(surface)),
        ws=bool(tok.whitespace_),
    )


def _analyze_batch_impl(pipeline: _LangPipeline, sentences: List[str]) -> List[AnalyzedSentence]:
    if not sentences:
        return []

    # Accentize per sentence and keep one cursor per sentence. A map shared
    # across the batch would make the first "замок" in the batch dictate the
    # stress of every later one.
    accented_full_list = [pipeline.accent(s) for s in sentences]
    cursors = [_AccentCursor(a) for a in accented_full_list]

    joined = _SEP.join(sentences)
    doc = pipeline.nlp(joined)

    chunks: List[List[Token]] = [[]]
    for tok in doc:
        if tok.text == _SEP_TOKEN:
            chunks.append([])
            continue
        ci = len(chunks) - 1
        chunks[-1].append(_make_token(tok, cursors[ci] if ci < len(cursors) else None))

    # Ensure we have exactly one chunk per input. If SpaCy ever merges or splits
    # an unexpected number of separators, fall back to per-sentence analysis.
    if len(chunks) != len(sentences):
        return [_analyze_single(pipeline, s, accented_full_list[i]) for i, s in enumerate(sentences)]

    return [
        AnalyzedSentence(text=accented_full_list[i], tokens=chunks[i])
        for i in range(len(sentences))
    ]


def _analyze_single(pipeline: _LangPipeline, sentence: str, accented_full: str) -> AnalyzedSentence:
    cursor = _AccentCursor(accented_full)
    doc = pipeline.nlp(sentence)
    return AnalyzedSentence(
        text=accented_full,
        tokens=[_make_token(tok, cursor) for tok in doc],
    )
