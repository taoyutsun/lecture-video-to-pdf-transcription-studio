from __future__ import annotations

APP_NAME = "Lecture Video to PDF & Transcription Studio"
APP_NAME_ZH = "課程影片轉講義 PDF 與逐字稿工具"
APP_EXECUTABLE_NAME = "LectureVideo2PDF"
APP_VERSION = "0.1.0"

AUTHOR_NAME = "Arthur Tao"
AUTHOR_BLOG_URL = "https://taoyutsun.blogspot.com/"
AUTHOR_FACEBOOK_URL = "https://facebook.com/arthurtaoyutsun"
SOURCE_REPO_URL = "https://github.com/taoyutsun/lecture-video-to-pdf-studio"
LICENSE_NAME = "MIT License"

AUTHOR_DESCRIPTION = (
    f"{APP_NAME} 由 {AUTHOR_NAME} 開發並以 {LICENSE_NAME} 授權。"
    "本工具聚焦於課程影片講義 PDF 擷取，並提供可獨立使用的語音轉文字功能。"
)

ASR_MODELS = [
    "tiny",
    "base",
    "small",
    "medium",
    "large-v2",
    "large-v3",
    "turbo",
    "distil-large-v3",
]

OPENAI_ASR_MODELS = [
    "whisper-large-v3",
    "whisper-large-v3-turbo",
    "distil-whisper-large-v3-en",
    "whisper-1",
    "default",
]

LANGUAGE_OPTIONS = [
    {"value": "", "label_zh": "自動偵測（中文優先繁體）", "label_en": "Auto detect (Traditional Chinese for Chinese)"},
    {"value": "zh-TW", "label_zh": "繁體中文", "label_en": "Traditional Chinese"},
    {"value": "zh-CN", "label_zh": "簡體中文", "label_en": "Simplified Chinese"},
    {"value": "en", "label_zh": "英文", "label_en": "English"},
    {"value": "ja", "label_zh": "日文", "label_en": "Japanese"},
    {"value": "ko", "label_zh": "韓文", "label_en": "Korean"},
    {"value": "fr", "label_zh": "法文", "label_en": "French"},
    {"value": "de", "label_zh": "德文", "label_en": "German"},
    {"value": "es", "label_zh": "西班牙文", "label_en": "Spanish"},
    {"value": "it", "label_zh": "義大利文", "label_en": "Italian"},
    {"value": "pt", "label_zh": "葡萄牙文", "label_en": "Portuguese"},
    {"value": "ru", "label_zh": "俄文", "label_en": "Russian"},
    {"value": "ar", "label_zh": "阿拉伯文", "label_en": "Arabic"},
]
