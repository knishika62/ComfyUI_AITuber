import os
import json
import re

CACHE_FILE = os.path.join(os.path.dirname(__file__), "aituber_personas_cache.json")


# ── データセット読み込み ──────────────────────────────────────────────────────

def _fetch_dataset() -> list:
    """HuggingFace datasets-server API から195件取得してキャッシュ保存"""
    try:
        import requests
    except ImportError:
        raise ImportError("requests パッケージが必要です: pip install requests")

    all_rows = []
    offset = 0
    length = 100

    print("[AITuber] データセットをダウンロード中...")
    while True:
        resp = requests.get(
            "https://datasets-server.huggingface.co/rows",
            params={
                "dataset": "DataPilot/AItuber-Personas-Japan",
                "config": "default",
                "split": "train",
                "offset": offset,
                "length": length,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])
        if not rows:
            break
        all_rows.extend(r["row"] for r in rows)
        print(f"[AITuber]  {len(all_rows)} 件取得済み...")
        if len(rows) < length:
            break
        offset += length

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=2)
    print(f"[AITuber] キャッシュ保存: {CACHE_FILE} ({len(all_rows)} 件)")
    return all_rows


def load_personas() -> list:
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return _fetch_dataset()


# ── concept パース ────────────────────────────────────────────────────────────

def extract_character_name(concept: str) -> str:
    """| 名前 | ... | 形式またはタイトルから名前を抽出"""
    m = re.search(r'\|\s*名前[^|]*\|\s*(.+?)\s*\|', concept)
    if m:
        return m.group(1).strip()
    m = re.search(r'^#\s+(.+?)(?:コンセプト設計書|設計書|\s*$)', concept, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return "Unknown"


def extract_visual_info(concept: str) -> str:
    """ビジュアルと音声要件セクションを抽出（なければ詳細ペルソナ）"""
    m = re.search(
        r'#{2,}\s*\d*[\.．]?\s*ビジュアル.+?\n(.*?)(?=\n#{2,}\s|\Z)',
        concept, re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    m = re.search(
        r'#{2,}\s*\d*[\.．]?\s*詳細ペルソナ.+?\n(.*?)(?=\n#{2,}\s|\Z)',
        concept, re.DOTALL,
    )
    if m:
        return m.group(1).strip()[:600]
    return concept[:600]


def extract_gender_label(concept: str) -> str:
    """性別表現フィールドから日本語ラベルを返す（不明は '不明'）"""
    # 1. 性別表現フィールドを直接探す
    m = re.search(r'性別表現[^\n|]*\|?\s*([^\n|]+)', concept)
    if m:
        val = m.group(1).strip()
        if val:
            return val

    # 2. 一人称から推定
    m = re.search(r'一人称[^\n|]*\|\s*([^\n|（(]+)', concept)
    if m:
        pronoun = m.group(1).strip()
        if re.search(r'俺|僕|ぼく|オレ', pronoun):
            return "男性的（一人称より推定）"
        if re.search(r'わたし|私|あたし|うち|ウチ|ボク|ぼく', pronoun):
            return "女性的（一人称より推定）"

    # 3. 性別・外見に関するキーワードをテキスト全体から探す
    if re.search(r'少女|女の子|女性的|女子|お嬢様|お姉さん|女装|ガール', concept):
        return "女性的（テキストより推定）"
    if re.search(r'少年|男の子|男性的|男子|青年|兄貴|オトコ', concept):
        return "男性的（テキストより推定）"

    return "不明"


def extract_gender_subject(concept: str) -> str:
    """性別表現からプロンプト冒頭の主語を決定"""
    label = extract_gender_label(concept)
    if re.search(r'女性|ボクっ娘|女の子|女子', label):
        return "realistic photo of a Japanese woman"
    if re.search(r'男性|男の子|男子', label):
        return "realistic photo of a Japanese man"
    if re.search(r'中性', label):
        return "realistic photo of an androgynous Japanese person"
    return "realistic photo of a Japanese person"


def _insert_gender_row(profile_text: str, gender_label: str) -> str:
    """テーブルの最終行直後に性別行を挿入する"""
    gender_row = f"| 性別 | {gender_label} |"
    lines = profile_text.splitlines()
    last_table_idx = -1
    for i, line in enumerate(lines):
        if line.strip().startswith("|"):
            last_table_idx = i
    if last_table_idx >= 0:
        lines.insert(last_table_idx + 1, gender_row)
        return "\n".join(lines)
    return profile_text + f"\n{gender_row}"


def build_character_summary(concept: str) -> str:
    """基本プロフィール + 詳細ペルソナの要点をまとめた概要テキストを生成"""
    sections = []
    gender_label = extract_gender_label(concept)
    gender_added = False

    # 基本プロフィール
    m = re.search(
        r'#{2,}\s*[^\n]*基本プロフィール[^\n]*\n(.*?)(?=\n#{2,}\s|\Z)',
        concept, re.DOTALL,
    )
    if m:
        profile_text = _insert_gender_row(m.group(1).strip(), gender_label)
        sections.append("【基本プロフィール】\n" + profile_text)
        gender_added = True

    # 詳細ペルソナ
    m = re.search(
        r'#{2,}\s*[^\n]*詳細ペルソナ[^\n]*\n(.*?)(?=\n#{2,}\s|\Z)',
        concept, re.DOTALL,
    )
    if m:
        sections.append("【詳細ペルソナ】\n" + m.group(1).strip())

    # 背景情報（冒頭200文字だけ）
    m = re.search(
        r'#{2,}\s*[^\n]*背景情報[^\n]*\n(.*?)(?=\n#{2,}\s|\Z)',
        concept, re.DOTALL,
    )
    if m:
        bg = m.group(1).strip()[:200]
        sections.append("【背景情報（抜粋）】\n" + bg + ("..." if len(m.group(1).strip()) > 200 else ""))

    if not sections:
        # どのセクションもマッチしない場合はフォールバック
        return f"| 性別 | {gender_label} |\n\n" + concept[:800]

    # 基本プロフィールが見つからなかった場合は先頭に性別だけ追記
    if not gender_added:
        sections.insert(0, f"【性別】{gender_label}")

    return "\n\n".join(sections)


def _strip_thinking(text: str) -> str:
    """<think>...</think> や /think ブロックを除去"""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<\|thinking\|>.*?<\|/thinking\|>', '', text, flags=re.DOTALL)
    return text.strip()


# ── system_prompt フィールド処理 ──────────────────────────────────────────────

def extract_sp_definition(sp_text: str) -> str:
    """会話例・ガードレール以前のキャラ定義部分だけを抽出（長すぎる場合は先頭1500文字）"""
    cutoff = re.search(
        r'\n#{1,4}\s*(会話例|良い例|悪い例|ガードレール|禁止事項|配信NGワード|Example|EXAMPLE)',
        sp_text, re.I,
    )
    if cutoff:
        return sp_text[:cutoff.start()].strip()
    return sp_text[:1500].strip()


def check_glasses(sp_text: str, concept: str) -> bool | None:
    """眼鏡の有無を判定（True=あり / False=なし / None=不明）"""
    combined = sp_text + concept
    if re.search(r'眼鏡なし|メガネなし|no glasses', combined, re.I):
        return False
    if re.search(r'眼鏡|メガネ|めがね|glasses|spectacles', combined, re.I):
        return True
    return None


# ── システムプロンプトテンプレート ────────────────────────────────────────────

_FEW_SHOT = (
    "Example 1:\n"
    "CHARACTER: Name: 潮凪 碧. Gender: male (boyish). Personality: low-energy, observant, seaside town origin. "
    "Color: deep blue, mint, coral. Outfit: oversized shirt, casual. Short messy hair.\n"
    "KEYWORDS: spring, casual, cafe\n"
    "OUTPUT: realistic photo of a Japanese man, short messy dark hair, calm sleepy expression, "
    "wearing oversized white linen shirt and beige chinos, sitting in a sunlit seaside cafe, "
    "soft natural light, spring afternoon, shell ear cuff, canvas tote bag with old books\n\n"
    "Example 2:\n"
    "CHARACTER: Name: 星宮小太郎. Gender: male. Personality: space enthusiast, business-mannered, samurai speech. "
    "Color: navy #1B365D, gold, warm skin. Outfit: summer wool suit, folding fan. Short neat hair.\n"
    "KEYWORDS: night, stargazing, rooftop\n"
    "OUTPUT: realistic photo of a Japanese man, short neat black hair, calm focused expression, "
    "wearing a tailored navy summer suit with gold cufflinks, standing on a rooftop under a star-filled night sky, "
    "city lights below, holding a folding fan with galaxy pattern, cool night breeze\n"
)

_SYSTEM_TEMPLATE = (
    "You are an expert image generation prompt writer for Stable Diffusion / FLUX.\n"
    "Given a Japanese character definition and keywords, write a high-quality English image prompt.\n\n"
    "Rules:\n"
    "- Output ONLY the prompt text — no explanation, no preamble, no quotes\n"
    "- English only\n"
    "- ALWAYS include: hairstyle (color + length + style), eye color if known, outfit details, "
    "setting, lighting\n"
    "- Add glasses ONLY if explicitly stated in the character definition. "
    "If not mentioned, do not include glasses\n"
    "- Keep it under 160 words, comma-separated phrases\n"
    "{gender_rule}\n\n"
    + _FEW_SHOT
)


# ── ComfyUI ノード ────────────────────────────────────────────────────────────

class AITuberPersonaPromptNode:
    _personas: list | None = None  # 起動中キャッシュ

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "index": ("INT", {
                    "default": 0, "min": 0, "max": 194, "step": 1,
                }),
                "keyword": ("STRING", {
                    "default": "春、カジュアル、カフェ",
                    "multiline": True,
                }),
                "api_base_url": ("STRING", {
                    "default": "http://192.168.11.200:1234/v1",
                    "multiline": False,
                }),
                "model_name": ("STRING", {
                    "default": "qwen3.5-122b-a10b",
                    "multiline": False,
                }),
                "api_key": ("STRING", {
                    "default": "",
                    "multiline": False,
                }),
                "max_tokens": ("INT", {
                    "default": 4096, "min": 256, "max": 8192, "step": 256,
                }),
                "temperature": ("FLOAT", {
                    "default": 0.7, "min": 0.0, "max": 2.0, "step": 0.05,
                }),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("character_name", "character_summary", "image_prompt")
    FUNCTION = "generate_prompt"
    CATEGORY = "AITuber"
    OUTPUT_NODE = False

    def generate_prompt(
        self,
        index: int,
        keyword: str,
        api_base_url: str,
        model_name: str,
        api_key: str,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, str, str]:

        # ── データ読み込み ──
        if AITuberPersonaPromptNode._personas is None:
            AITuberPersonaPromptNode._personas = load_personas()

        personas = AITuberPersonaPromptNode._personas
        idx = max(0, min(index, len(personas) - 1))
        persona = personas[idx]

        concept    = persona.get("concept", "")
        sp_field   = persona.get("system_prompt", "")

        character_name    = extract_character_name(concept)
        visual_info       = extract_visual_info(concept)
        character_summary = build_character_summary(concept)
        gender_subject    = extract_gender_subject(concept)

        # ── キャラ情報を組み立て ──
        sp_definition = extract_sp_definition(sp_field)
        glasses       = check_glasses(sp_field, concept)

        glasses_rule = "glasses" if glasses is True else None

        # system_prompt定義 + conceptビジュアルを結合してLLMに渡す
        char_info = (
            f"## Character Definition\n{sp_definition}\n\n"
            f"## Visual Design Details\n{visual_info}"
        )
        if glasses_rule:
            char_info += f"\n\nGlasses: {glasses_rule}"

        # ── LLM でプロンプト生成 ──
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai パッケージが必要です: pip install openai")

        import httpx
        client = OpenAI(
            base_url=api_base_url.rstrip("/"),
            api_key=api_key.strip() if api_key.strip() else "dummy",
            http_client=httpx.Client(timeout=120.0),
        )

        system_prompt = _SYSTEM_TEMPLATE.format(
            gender_rule=f'- MUST start with exactly: "{gender_subject},"'
        )

        user_message = (
            f"CHARACTER DEFINITION:\n{char_info}\n\n"
            f"KEYWORDS: {keyword}\n\n"
            "OUTPUT:"
        )

        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )

        raw = response.choices[0].message.content or ""
        image_prompt = _strip_thinking(raw)

        gender_label = extract_gender_label(concept)
        print(f"[AITuber] index={idx} name={character_name} gender={gender_label} glasses={glasses}")
        print(f"[AITuber] prompt={image_prompt[:80]}...")

        return (character_name, character_summary, image_prompt)
