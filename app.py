import os
import tempfile
import time
import mimetypes
from PIL import Image
from google import genai
from google.genai import types
import streamlit as st

# --------------------------------------------------------------------------------
# 1. ページ基本設定
# --------------------------------------------------------------------------------
st.set_page_config(
    page_title="MiniMax H3 Prompt Generator", page_icon="🎬", layout="wide"
)

st.title("🎬 MiniMax H3 プロンプトメーカー")
st.caption(
    "MiniMax H3の公式規格に完全準拠した英語プロンプト＆和訳を自動生成します。"
)

# --------------------------------------------------------------------------------
# 定数
# --------------------------------------------------------------------------------
MAX_IMGS = 9
MAX_VIDS = 3
MAX_AUDS = 3
MAX_TOTAL = 12
FILE_PROCESSING_TIMEOUT_SEC = 120  # ファイル処理待ちのタイムアウト
FILE_PROCESSING_POLL_INTERVAL_SEC = 2

# --------------------------------------------------------------------------------
# ヘルパー関数
# --------------------------------------------------------------------------------
def get_mime_type(uploaded_file):
    """Streamlitのfile.typeがNoneの場合、拡張子から推測してフォールバックする"""
    if uploaded_file.type:
        return uploaded_file.type
    guessed, _ = mimetypes.guess_type(uploaded_file.name)
    return guessed or "application/octet-stream"


def upload_via_file_api(client, uploaded_file):
    """動画・音声ファイルを一時保存してFile API経由でアップロードする"""
    ext = os.path.splitext(uploaded_file.name)[1] or ""
    mime_type = get_mime_type(uploaded_file)
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = tmp.name

    try:
        gfile = client.files.upload(
            file=tmp_path,
            config=types.UploadFileConfig(mime_type=mime_type),
        )
    except TypeError:
        # SDKバージョン差異のフォールバック（config非対応の場合）
        gfile = client.files.upload(file=tmp_path)

    return gfile, tmp_path


def wait_for_file_active(client, gfile, timeout_sec=FILE_PROCESSING_TIMEOUT_SEC):
    """アップロード済みファイルの処理完了を待機する（タイムアウト付き）"""
    elapsed = 0
    current = gfile
    while "PROCESSING" in str(current.state):
        if elapsed >= timeout_sec:
            raise TimeoutError(
                f"ファイル処理がタイムアウトしました（{timeout_sec}秒）: {current.name}"
            )
        time.sleep(FILE_PROCESSING_POLL_INTERVAL_SEC)
        elapsed += FILE_PROCESSING_POLL_INTERVAL_SEC
        current = client.files.get(name=current.name)

    if "FAILED" in str(current.state):
        raise ValueError(f"ファイル処理に失敗しました: {current.name}")

    return current


def call_gemini(client, prompt_inputs, model="gemini-2.5-flash"):
    """Gemini APIを呼び出し、エラー時は分かりやすいメッセージにして再送出する"""
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt_inputs,
        )
        if not getattr(response, "text", None):
            raise ValueError(
                "APIから空の応答が返されました。入力内容やAPIキーを確認してください。"
            )
        return response
    except Exception as e:
        raise RuntimeError(f"Gemini API呼び出し中にエラーが発生しました: {e}") from e


def render_result(response_text):
    """生成結果を英語プロンプト／日本語訳のタブで表示する共通関数"""
    st.markdown("---")
    st.subheader("✨ 生成結果")
    if "===JAPANESE_TRANSLATION===" in response_text:
        parts = response_text.split("===JAPANESE_TRANSLATION===")
        tab_en, tab_ja = st.tabs(["📋 生成プロンプト (英語)", "🇯🇵 日本語訳"])
        with tab_en:
            st.code(
                parts[0].replace("===ENGLISH_PROMPT===", "").strip(),
                language="text",
            )
        with tab_ja:
            st.markdown(parts[1].strip())
    else:
        st.code(response_text, language="text")


def cleanup_temp_files(temp_files):
    for tmp_path in temp_files:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# --------------------------------------------------------------------------------
# 2. モード選択（FL2VA ➔ Ref2VAの順）
# --------------------------------------------------------------------------------
mode = st.radio(
    "生成モードを選択してください：",
    [
        "🎞️ FL2VA (T2V / I2V / FL2V: キーフレーム補間)",
        "🔥 Ref2VA (オムニ参照: キャラの固定・音声合成)",
    ],
    index=0,
    horizontal=True,
)

st.markdown("---")

# --------------------------------------------------------------------------------
# 3. 使い方・ガイドテキスト (モード連動)
# --------------------------------------------------------------------------------
with st.expander(
    "📖 アプリの使い方と入力例（初めての方はこちらを開いてください）",
    expanded=True,
):
    if "FL2VA" in mode:
        st.markdown("""
### 💡 FL2VAモードの使い方 (T2V / I2V / FL2V)
画像を全く入れなければ「テキストからの動画生成(T2V)」になります。**「1stフレーム（開始）」**と**「Lastフレーム（終了）」**の最大2枚を指定すると、その間の変化やアクションを補間するプロンプトを生成します。

- **記入例（T2V / 画像なし）**：
  > 「映画のようなカメラワーク。宇宙船のブリッジで、青い髪の女性艦長が外の星団を眺めている。激しい爆発音が響く。」
- **記入例（FL2V / 画像2枚）**：
  > 「`<Picture 1>`の閉じた宝箱が、5秒かけて光を放ちながら開き、`<Picture 2>`の輝く宝石で満たされた状態になる。激しい効果音とともにカメラが寄る。」
""")
    else:
        st.markdown("""
### 💡 Ref2VAモードの使い方
画像（最大9枚）・動画（最大3個）・音声（最大3個）を参照素材として指定し、キャラクターの顔、声質、BGMなどを固定・合成するプロンプトを生成します。

- **記入例（概要欄）**：
  > 「`<Picture 1>`の女の子が、`<Picture 2>`のカフェで`<Audio 1>`の声質で『こんにちは！』と挨拶する。」

⚠️ 音声ファイル単体では使用できません。必ず画像または動画と一緒にアップロードしてください。
""")

# --------------------------------------------------------------------------------
# 4. サイドバー設定 (APIキー入力)
# --------------------------------------------------------------------------------
st.sidebar.header("🔑 APIキー設定")
api_key = st.sidebar.text_input("Gemini API Key を入力", type="password")
st.sidebar.markdown(
    "<small>※入力されたキーは生成のみに使用され、保存されません。</small>",
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------------
# 5. メイン処理部
# --------------------------------------------------------------------------------
if api_key:
    # 新SDKのクライアント初期化
    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        st.error(f"APIクライアントの初期化に失敗しました: {e}")
        st.stop()

    # ============================================================================
    # A. FL2VA モードの処理 (T2V / I2V)
    # ============================================================================
    if "FL2VA" in mode:
        st.subheader("1. キーフレーム画像のアップロード（T2Vの場合は不要）")
        col1, col2 = st.columns(2)
        with col1:
            first_frame = st.file_uploader(
                "🖼️ 1stフレーム（開始画像 / <Picture 1>）",
                type=["png", "jpg", "jpeg", "webp"],
                accept_multiple_files=False,
            )
        with col2:
            last_frame = st.file_uploader(
                "🖼️ Lastフレーム（終了画像 / <Picture 2>）",
                type=["png", "jpg", "jpeg", "webp"],
                accept_multiple_files=False,
            )

        st.markdown("---")
        st.subheader("2. 動画のストーリー・フレーム間の変化を指定")
        user_summary = st.text_area(
            "作りたい動画の展開、または1stからLastへ向けての変化・アクションを入力してください（日本語OK）",
            placeholder="例：<Picture 1>のキャラクターが剣を振り下ろし、爆発とともに<Picture 2>のポーズに変化する。",
        )

        if st.button("🚀 FL2VAプロンプトを自動生成"):
            if not first_frame and not last_frame and not user_summary.strip():
                st.warning("テキスト概要、または画像を入力してください。")
            else:
                with st.spinner("FL2VA公式規格（3ブロック構造）へ変換中..."):
                    try:
                        prompt_inputs = []
                        frame_info = ""

                        if first_frame:
                            prompt_inputs.append(
                                types.Part.from_bytes(
                                    data=first_frame.getvalue(),
                                    mime_type=get_mime_type(first_frame),
                                )
                            )
                            frame_info += "- <Picture 1>: 1stフレーム（開始画像）\n"
                        if last_frame:
                            prompt_inputs.append(
                                types.Part.from_bytes(
                                    data=last_frame.getvalue(),
                                    mime_type=get_mime_type(last_frame),
                                )
                            )
                            frame_info += f"- <Picture {'2' if first_frame else '1'}>: Lastフレーム（終了画像）\n"

                        system_prompt = f"""
あなたはMiniMax H3 FL2VA (First-and-Last-Frame Mode) 専門プロンプト生成AIです。
提供されたキーフレーム画像情報と指示から、FL2VA公式規格(3ブロック構造)の英語プロンプトと和訳を出力してください。

【フレーム情報】
{frame_info if frame_info else "テキストのみのT2V生成"}

【FL2VA公式必須フォーマット】
以下の3つのブロックのみを出力してください（subject_definitions や retention_analysis は絶対に使用しないでください）。

===ENGLISH_PROMPT===
integrated_multimodal_description:
<T2Vの場合は画像の言及不要。画像がある場合は「<Picture 1> is the actual first frame...」等を記述>
[Shot 1] <被写体の動き、アクション、カメラワークを詳細に記述。セリフがある場合は <d>[Japanese] 「...」</d> タグを使用>

overall_soundscape:
<動きに伴う物理音、効果音(SE)、環境音>

non_diegetic_music:
<BGM指定>

===JAPANESE_TRANSLATION===
【日本語訳・解説】
<セクションごとの和訳>

【ユーザーの動画指示】
{user_summary}
"""
                        prompt_inputs.insert(0, system_prompt)
                        response = call_gemini(client, prompt_inputs)
                        render_result(response.text)

                    except Exception as e:
                        st.error(f"エラーが発生しました: {e}")

    # ============================================================================
    # B. Ref2VA モードの処理 (マルチ参照)
    # ============================================================================
    else:
        st.subheader("1. 参照ファイルのアップロード（合計最大12個まで）")
        col1, col2, col3 = st.columns(3)
        with col1:
            img_files = st.file_uploader(
                f"🖼️ 画像（最大{MAX_IMGS}枚）",
                type=["png", "jpg", "jpeg", "webp"],
                accept_multiple_files=True,
            )
        with col2:
            vid_files = st.file_uploader(
                f"🎥 動画（最大{MAX_VIDS}個 / 各2~15秒）",
                type=["mp4", "mov"],
                accept_multiple_files=True,
            )
        with col3:
            aud_files = st.file_uploader(
                f"🎵 音声（最大{MAX_AUDS}個 / 各2~15秒）",
                type=["wav", "mp3", "m4a", "aac", "ogg", "flac"],
                accept_multiple_files=True,
            )

        num_imgs = len(img_files) if img_files else 0
        num_vids = len(vid_files) if vid_files else 0
        num_auds = len(aud_files) if aud_files else 0
        total_files = num_imgs + num_vids + num_auds

        # --- バリデーション ---
        validation_errors = []
        if num_imgs > MAX_IMGS:
            validation_errors.append(
                f"画像は最大{MAX_IMGS}枚までです（現在: {num_imgs}枚）。"
            )
        if num_vids > MAX_VIDS:
            validation_errors.append(
                f"動画は最大{MAX_VIDS}個までです（現在: {num_vids}個）。"
            )
        if num_auds > MAX_AUDS:
            validation_errors.append(
                f"音声は最大{MAX_AUDS}個までです（現在: {num_auds}個）。"
            )
        if total_files > MAX_TOTAL:
            validation_errors.append(
                f"ファイル合計が{MAX_TOTAL}個を超えています（現在: {total_files}個）。"
            )
        if num_auds > 0 and num_imgs == 0 and num_vids == 0:
            validation_errors.append(
                "音声ファイル単体では使用できません。必ず画像または動画と一緒にアップロードしてください。"
            )

        if validation_errors:
            for err in validation_errors:
                st.error(f"⚠️ {err}")
        else:
            file_instructions = []
            if total_files > 0:
                st.markdown("---")
                st.subheader("2. 各ファイルの「役割」と「使用部分」を指定")
                if img_files:
                    st.markdown("#### 🖼️ 画像のガイド")
                    for i, file in enumerate(img_files, 1):
                        c1, c2 = st.columns([1, 2])
                        with c1:
                            role = st.selectbox(
                                f"<Picture {i}> の役割",
                                ["人物・身元", "衣装・小道具", "背景・環境", "構図", "スタイル"],
                                key=f"r_img_{i}",
                            )
                        with c2:
                            detail = st.text_input(
                                f"<Picture {i}> のどの部分を使うか？",
                                placeholder="例：顔と髪型だけ、服装だけ",
                                key=f"r_img_d_{i}",
                            )
                        file_instructions.append(
                            f"- <Picture {i}>: 役割=[{role}], 指示=[{detail if detail else '全体'}]"
                        )

                if vid_files:
                    st.markdown("#### 🎥 動画のガイド")
                    for i, file in enumerate(vid_files, 1):
                        c1, c2 = st.columns([1, 2])
                        with c1:
                            role = st.selectbox(
                                f"<Video {i}> の役割",
                                ["ダンス・アクション", "カメラワーク", "全体構造"],
                                key=f"r_vid_{i}",
                            )
                        with c2:
                            detail = st.text_input(
                                f"<Video {i}> のどの部分を使うか？",
                                placeholder="例：ダンスモーションだけ",
                                key=f"r_vid_d_{i}",
                            )
                        file_instructions.append(
                            f"- <Video {i}>: 役割=[{role}], 指示=[{detail if detail else '全体の動き'}]"
                        )

                if aud_files:
                    st.markdown("#### 🎵 音声のガイド")
                    for i, file in enumerate(aud_files, 1):
                        c1, c2 = st.columns([1, 2])
                        with c1:
                            role = st.selectbox(
                                f"<Audio {i}> の役割",
                                ["声質（クローン）", "リップシンク用ボーカル", "BGM", "効果音"],
                                key=f"r_aud_{i}",
                            )
                        with c2:
                            detail = st.text_input(
                                f"<Audio {i}> のどの部分を使うか？",
                                placeholder="例：声質だけ、テンポだけ",
                                key=f"r_aud_d_{i}",
                            )
                        file_instructions.append(
                            f"- <Audio {i}>: 役割=[{role}], 指示=[{detail if detail else '全体の音'}]"
                        )

            st.markdown("---")
            st.subheader("3. 動画の全体ストーリー・概要")
            user_summary = st.text_area(
                "動画のストーリーやセリフ（日本語OK）",
                placeholder="例：<Picture 1>の女の子が<Audio 1>の声で喋る",
            )

            if st.button("🚀 Ref2VAプロンプトを自動生成"):
                if total_files == 0 and not user_summary.strip():
                    st.warning("参照ファイル、または動画概要のどちらかを入力してください。")
                else:
                    with st.spinner("Ref2VA公式規格（6セクション構造）へ変換中..."):
                        prompt_inputs = []
                        temp_files = []
                        uploaded_files = []

                        try:
                            # 1. 画像の追加（types.Part.from_bytes を使用）
                            if img_files:
                                for f in img_files:
                                    prompt_inputs.append(
                                        types.Part.from_bytes(
                                            data=f.getvalue(),
                                            mime_type=get_mime_type(f),
                                        )
                                    )

                            # 2. 動画の追加（File API経由）
                            if vid_files:
                                for f in vid_files:
                                    gfile, tmp_path = upload_via_file_api(client, f)
                                    temp_files.append(tmp_path)
                                    uploaded_files.append(gfile)
                                    prompt_inputs.append(gfile)

                            # 3. 音声の追加（File API経由）
                            if aud_files:
                                for f in aud_files:
                                    gfile, tmp_path = upload_via_file_api(client, f)
                                    temp_files.append(tmp_path)
                                    uploaded_files.append(gfile)
                                    prompt_inputs.append(gfile)

                            # 4. アップロードされた大容量メディアの処理完了待機（タイムアウト付き）
                            for idx, u_file in enumerate(uploaded_files):
                                uploaded_files[idx] = wait_for_file_active(client, u_file)

                            formatted_instructions = "\n".join(file_instructions)
                            system_prompt = f"""
あなたはMiniMax H3 Ref2VA(Omni-Reference Mode)専門プロンプト生成AIです。
以下の素材指示と動画概要から、Ref2VA公式規格(6セクション構造)の英語プロンプトと和訳を出力してください。

【素材指示】
{formatted_instructions}

【フォーマット】
===ENGLISH_PROMPT===
subject_definitions:
<定義文>

summary:
<全体概要>

retention_analysis:
<保持分析 (fully_preserved等)>

detailed_description:
<詳細・カメラワーク・セリフは <d>[Japanese] 「...」</d> タグ>

overall_soundscape:
<効果音・環境音>

non_diegetic_music:
<BGM指定>

===JAPANESE_TRANSLATION===
【日本語訳・解説】
<セクションごとの和訳>

【ユーザーの動画概要】
{user_summary}
"""
                            prompt_inputs.insert(0, system_prompt)
                            response = call_gemini(client, prompt_inputs)
                            render_result(response.text)

                        except Exception as e:
                            st.error(f"エラーが発生しました: {e}")

                        finally:
                            # サーバー内の一時ファイルを削除
                            cleanup_temp_files(temp_files)

else:
    st.info("👈 左側のサイドバーに Gemini API Key を入力してください。")
