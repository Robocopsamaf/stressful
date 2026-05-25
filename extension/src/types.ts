export interface Settings {
  enabled: boolean;
  targetLang: string;
  backendUrl: string;
  showStress: boolean;
  showColors: boolean;
  showTooltips: boolean;
  overlayPosition: number;
}

export const DEFAULT_SETTINGS: Settings = {
  enabled: true,
  targetLang: "en",
  backendUrl: "http://localhost:8765",
  showStress: true,
  showColors: true,
  showTooltips: true,
  overlayPosition: 8,
};

export interface Cue {
  start: number;
  dur: number;
  text: string;
}

export interface Token {
  surface: string;
  accented: string;
  lemma: string;
  pos: string;
  morph: Record<string, string>;
  color_class: string;
  is_word: boolean;
}

export interface AnalyzedSentence {
  text: string;
  tokens: Token[];
}

export interface AnalyzeResponse {
  sentences: AnalyzedSentence[];
}

export interface CaptionTrackInfo {
  baseUrl: string;
  languageCode: string;
  kind?: string;
  name?: string;
}

export type RuntimeMessage =
  | { type: "getSettings" }
  | { type: "setSettings"; settings: Partial<Settings> };

export interface LangOption {
  code: string;
  label: string;
}

export const LANGS: LangOption[] = [
  { code: "en", label: "English" },
  { code: "de", label: "German" },
  { code: "es", label: "Spanish" },
  { code: "fr", label: "French" },
  { code: "it", label: "Italian" },
  { code: "pl", label: "Polish" },
  { code: "pt", label: "Portuguese" },
  { code: "sv", label: "Swedish" },
  { code: "nl", label: "Dutch" },
  { code: "uk", label: "Ukrainian" },
  { code: "tr", label: "Turkish" },
  { code: "ja", label: "Japanese" },
  { code: "zh-Hans", label: "Chinese (Simplified)" },
  { code: "ko", label: "Korean" },
];
