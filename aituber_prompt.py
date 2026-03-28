#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
aituber_prompt.py
-----------------
DataPilot/AItuber-Personas-Japan のキャラ定義から
英語画像生成プロンプトを自動生成する CLI。
ComfyUI ノード（aituber_persona_node.py）と同一ロジック。

使い方例:
    $ python aituber_prompt.py -i 0 -k "春、カジュアル、カフェ"
    $ python aituber_prompt.py -n "潮凪 碧" -k "夜、ネオン、雨" -f json
    $ python aituber_prompt.py -i 6 -k "stargazing, rooftop"
"""

import argparse
import json
import re
import sys
from pathlib import Path

import httpx

# ── デフォルト設定（config.yaml があれば上書き） ──────────────────────────────
DEFAULT_CFG = {
    "api_base":    "http://192.168.11.200:1234/v1",
    "model":       "qwen3.5-122b-a10b",
    "api_key":     "",
    "temperature": 0.7,
    "max_tokens":  4096,
}

CONFIG_PATH = Path(__file__).with_name("config.yaml")
if CONFIG_PATH.is_file():
    try:
        import yaml
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            DEFAULT_CFG.update(yaml.safe_load(f))
    except ImportError:
        pass  # yaml 未インストール時は無視


# ── データセット読み込み（JSONキャッシュ） ────────────────────────────────────
CACHE_FILE = Path(__file__).with_name("aituber_personas_cache.json")

def _fetch_dataset() -> list:
    print("[AITuber] データセットをダウンロード中...", file=sys.stderr)
    all_rows = []
    offset, length = 0, 100
    while True:
        resp = httpx.get(
            "https://datasets-server.huggingface.co/rows",
            params={
                "dataset": "DataPilot/AItuber-Personas-Japan",
                "config": "default",
                "split": "train",
                "offset": offset,
                "length": length,
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        rows = resp.json().get("rows", [])
        if not rows:
            break
        all_rows.extend(r["row"] for r in rows)
        print(f"[AITuber]  {len(all_rows)} 件取得済み...", file=sys.stderr)
        if len(rows) < length:
            break
        offset += length
    CACHE_FILE.write_text(
        json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[AITuber] キャッシュ保存: {CACHE_FILE}", file=sys.stderr)
    return all_rows


def load_personas() -> list:
    if CACHE_FILE.is_file():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return _fetch_dataset()


def find_by_index(personas: list, idx: int) -> dict | None:
    return personas[idx] if 0 <= idx < len(personas) else None


def find_by_name(personas: list, query: str) -> dict | None:
    pat = re.compile(re.escape(query), re.IGNORECASE)
    for p in personas:
        if pat.search(p.get("concept", "")) or pat.search(p.get("system_prompt", "")):
            return p
    return None


# ── concept パース ────────────────────────────────────────────────────────────

def extract_character_name(concept: str) -> str:
    m = re.search(r'\|\s*名前[^|]*\|\s*(.+?)\s*\|', concept)
    if m:
        return m.group(1).strip()
    m = re.search(r'^#\s+(.+?)(?:コンセプト設計書|設計書|\s*$)', concept, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return "Unknown"


def extract_visual_info(concept: str) -> str:
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
    m = re.search(r'性別表現[^\n|]*\|?\s*([^\n|]+)', concept)
    if m:
        val = m.group(1).strip()
        if val:
            return val
    m = re.search(r'一人称[^\n|]*\|\s*([^\n|（(]+)', concept)
    if m:
        pronoun = m.group(1).strip()
        if re.search(r'俺|僕|ぼく|オレ', pronoun):
            return "男性的（一人称より推定）"
        if re.search(r'わたし|私|あたし|うち|ウチ|ボク|ぼく', pronoun):
            return "女性的（一人称より推定）"
    if re.search(r'少女|女の子|女性的|女子|お嬢様|お姉さん|女装|ガール', concept):
        return "女性的（テキストより推定）"
    if re.search(r'少年|男の子|男性的|男子|青年|兄貴|オトコ', concept):
        return "男性的（テキストより推定）"
    return "不明"


def extract_gender_subject(concept: str) -> str:
    label = extract_gender_label(concept)
    if re.search(r'女性|ボクっ娘|女の子|女子', label):
        return "realistic photo of a Japanese woman"
    if re.search(r'男性|男の子|男子', label):
        return "realistic photo of a Japanese man"
    if re.search(r'中性', label):
        return "realistic photo of an androgynous Japanese person"
    return "realistic photo of a Japanese person"


# ── system_prompt フィールド処理 ──────────────────────────────────────────────

def extract_sp_definition(sp_text: str) -> str:
    """会話例・ガードレール以前のキャラ定義部分のみ抽出"""
    cutoff = re.search(
        r'\n#{1,4}\s*(会話例|良い例|悪い例|ガードレール|禁止事項|配信NGワード|Example|EXAMPLE)',
        sp_text, re.I,
    )
    if cutoff:
        return sp_text[:cutoff.start()].strip()
    return sp_text[:1500].strip()


def check_glasses(sp_text: str, concept: str) -> bool | None:
    """眼鏡の有無（True=あり / False=なし / None=不明）"""
    combined = sp_text + concept
    if re.search(r'眼鏡なし|メガネなし|no glasses', combined, re.I):
        return False
    if re.search(r'眼鏡|メガネ|めがね|glasses|spectacles', combined, re.I):
        return True
    return None


# ── プロンプトテンプレート（Node版と共通） ────────────────────────────────────

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


def _strip_thinking(text: str) -> str:
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<\|thinking\|>.*?<\|/thinking\|>', '', text, flags=re.DOTALL)
    return text.strip()


# ── LLM 呼び出し ─────────────────────────────────────────────────────────────

def call_llm(system_prompt: str, user_message: str) -> str:
    api_key = DEFAULT_CFG["api_key"].strip() or "dummy"
    url = f"{DEFAULT_CFG['api_base'].rstrip('/')}/chat/completions"
    payload = {
        "model":       DEFAULT_CFG["model"],
        "messages":    [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
        "temperature": DEFAULT_CFG["temperature"],
        "max_tokens":  DEFAULT_CFG["max_tokens"],
    }
    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(url, json=payload,
                               headers={"Authorization": f"Bearer {api_key}"})
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"] or ""
            return _strip_thinking(raw)
    except Exception as e:
        print(f"[ERROR] LLM request failed: {e}", file=sys.stderr)
        sys.exit(1)


# ── 出力整形 ──────────────────────────────────────────────────────────────────

def output_result(prompt: str, name: str, fmt: str) -> None:
    if fmt == "plain":
        print(prompt)
    elif fmt == "json":
        json.dump({"name": name, "prompt": prompt},
                  sys.stdout, ensure_ascii=False, indent=2)
        print()
    elif fmt == "yaml":
        try:
            import yaml
            yaml.safe_dump({"name": name, "prompt": prompt},
                           sys.stdout, allow_unicode=True, sort_keys=False)
        except ImportError:
            print(f"name: {name}\nprompt: {prompt}")
    else:
        raise ValueError(f"Unsupported format: {fmt}")


# ── メイン ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="AITuber キャラ定義から英語画像生成プロンプトを生成する CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("-i", "--index", type=int,
                     help="キャラクタ番号（0ベース）")
    grp.add_argument("-n", "--name",  type=str,
                     help="キャラクタ名で検索（部分一致）")

    parser.add_argument("-k", "--keyword", type=str, default="",
                        help="テーマキーワード（カンマ区切り or 日本語）")
    parser.add_argument("-f", "--format",
                        choices=["plain", "json", "yaml"], default="plain",
                        help="出力形式（デフォルト: plain）")
    parser.add_argument("--refresh", action="store_true",
                        help="キャッシュを無視して再ダウンロード")
    args = parser.parse_args()

    # キャッシュ削除（--refresh）
    if args.refresh and CACHE_FILE.is_file():
        CACHE_FILE.unlink()

    # データ取得
    personas = load_personas()

    # キャラ選択
    if args.index is not None:
        persona = find_by_index(personas, args.index)
        if persona is None:
            print(f"[ERROR] index {args.index} は範囲外 (0-{len(personas)-1})",
                  file=sys.stderr)
            sys.exit(1)
    else:
        persona = find_by_name(personas, args.name)
        if persona is None:
            print(f'[ERROR] "{args.name}" に一致するキャラが見つかりません',
                  file=sys.stderr)
            sys.exit(1)

    # フィールド取得
    concept  = persona.get("concept", "")
    sp_field = persona.get("system_prompt", "")

    character_name  = extract_character_name(concept)
    visual_info     = extract_visual_info(concept)
    gender_subject  = extract_gender_subject(concept)
    sp_definition   = extract_sp_definition(sp_field)
    glasses         = check_glasses(sp_field, concept)

    glasses_rule = "glasses" if glasses is True else None

    # キャラ情報組み立て
    char_info = (
        f"## Character Definition\n{sp_definition}\n\n"
        f"## Visual Design Details\n{visual_info}"
    )
    if glasses_rule:
        char_info += f"\n\nGlasses: {glasses_rule}"

    # プロンプト生成
    system_prompt = _SYSTEM_TEMPLATE.format(
        gender_rule=f'- MUST start with exactly: "{gender_subject},"'
    )
    user_message = (
        f"CHARACTER DEFINITION:\n{char_info}\n\n"
        f"KEYWORDS: {args.keyword}\n\n"
        "OUTPUT:"
    )

    gender_label = extract_gender_label(concept)
    print(f"[AITuber] index={args.index} name={character_name} gender={gender_label} glasses={glasses}",
          file=sys.stderr)
    print(file=sys.stderr)

    image_prompt = call_llm(system_prompt, user_message)

    output_result(image_prompt, character_name, args.format)
    print(file=sys.stderr)


if __name__ == "__main__":
    main()
