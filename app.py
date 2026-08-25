import os
import tempfile
import time
import mimetypes
from PIL import Image
from google import genai
from google.genai import types
import streamlit as st

# --------------------------------------------------------------------------------
# 1. ページ基本設定・状態管理
# --------------------------------------------------------------------------------
st.set_page_config(
    page_title="MiniMax H3 Prompt Generator", page_icon="🎬", layout="wide"
)

st.title("🎬 MiniMax H3 プロンプトメーカー")
st.caption(
    "MiniMax H3の公式規格に完全準拠した英語プロンプト＆和訳を自動生成します。"
)

# セッション状態の初期化
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "current_mode" not in st.session_state:
    st.session_state.current_mode = None

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
def load_rule_file(filename):
    """公式ルールが記述されたMarkdownファイルを読み込む"""
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            return f.read()
    else:
        st.error(f"致命的なエラー: 必須ルールファイル '{filename}' がディレクトリに見つかりません。プログラムを停止します。")
        st.stop()

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
        # SDKバージョン差異のフォールバック
        gfile = client.files.upload(file=tmp_path)

    return gfile, tmp_path

def wait_for_file_active(client, gfile, timeout_sec=FILE_PROCESSING_TIMEOUT_SEC):
    """アップロード済みファイルの処理完了を待機する"""
    elapsed = 0
    current = gfile
    while "PROCESSING" in str(current.state):
        if elapsed >= timeout_sec:
            raise TimeoutError(f"ファイル処理がタイムアウトしました（{timeout_sec}秒）: {current.name}")
        time.sleep(FILE_PROCESSING_POLL_INTERVAL_SEC)
        elapsed += FILE_PROCESSING_POLL_INTERVAL_SEC
        current = client.files.get(name=current.name)

    if "FAILED" in str(current.state):
        raise ValueError(f"ファイル処理に失敗しました: {current.name}")

    return current

def build_user_content(prompt_inputs):
    """Geminiに渡す入力を安全なtypes.Contentオブジェクトに変換する"""
    user_parts = []
    for item in prompt_inputs:
        if isinstance(item, str):
            user_parts.append(types.Part.from_text(text=item))
        elif hasattr(item, "uri"):
            # File APIオブジェクトの場合はURI参照に変換
            user_parts.append(types.Part.from_uri(file_uri=item.uri, mime_type=item.mime_type))
        else:
            # 既に types.Part (画像など) の場合
            user_parts.append(item)
    return types.Content(role="user", parts=user_parts)

def call_gemini(client, contents, model="gemini-3.5-flash-lite"):
    """Gemini APIを呼び出し、レスポンスを返す（temperature=0.0でハルシネーションと描写を完全封鎖）"""
    try:
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.0,  # 決定論的出力にし、ルール違反や画像描写の勝手な追加・セリフの捏造を完全防止
            )
        )
        if not getattr(response, "text", None):
            raise ValueError("APIから空の応答が返されました。入力内容やAPIキーを確認してください。")
        return response
    except Exception as e:
        raise RuntimeError(f"Gemini API呼び出し中にエラーが発生しました: {e}") from e

def render_result(response_text):
    """生成結果を英語プロンプト／日本語訳のタブで表示する"""
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
# オプションUI生成ヘルパー（スタイル選択とテキスト演出）
# --------------------------------------------------------------------------------
def render_style_and_text_options():
    st.markdown("#### 🎨 演出・スタイル設定（オプション）")
    style_options = [
        "指定なし",
        "Cinematic (映画風・重厚感)",
        "Realistic Live-Action (リアルな実写風)",
        "Music Video (ミュージックビデオ風)",
        "2D Animation (2Dアニメ風)",
        "3D CG (3Dアニメ風)",
        "Cyberpunk / Sci-Fi (サイバーパンク・SF風)",
        "Vintage / Retro Film (ヴィンテージ・レトロフィルム風)",
        "Minimalist / Commercial (ミニマリスト・商用風)"
    ]
    video_style = st.selectbox("映像のスタイル", style_options)

    use_text = st.checkbox("🔤 画面内テキスト・歌詞アニメーションを追加する")
    overlay_text = ""
    text_style = ""
    if use_text:
        overlay_text = st.text_input("表示するテキスト / 歌詞 (英語または日本語)", placeholder="例: Color Meets Sound")
        text_style = st.selectbox(
            "デザイン・動きのスタイル",
            [
                "フェードイン / スライドイン（滑らかな登場）",
                "キネティック・タイポグラフィ（リズムに合わせた文字アニメーション）",
                "ネオンサイン風 / サイバー発光テキスト",
                "ミニマルなクリーン・サブタイトル"
            ]
        )
    return video_style, use_text, overlay_text, text_style

def build_enhanced_summary(base_summary, video_style, use_text, overlay_text, text_style):
    enhanced = f"【ユーザーの動画概要】\n{base_summary}\n"
    if video_style != "指定なし":
        enhanced += f"\n【指定映像スタイル】\n全体を「{video_style}」のトーンと演出で統一してください。\n"
    if use_text and overlay_text.strip():
        enhanced += f"\n【画面内テキスト・タイポグラフィ指定】\nテキスト内容: {overlay_text.strip()}\n演出スタイル: {text_style}\n"
        enhanced += "※指示: 上記テキストを動画内に表示されるインフレーム・タイポグラフィとして詳細描写に組み込んでください。公式ルールに従い、画面内テキストは二重引用符（\"\"）で囲むか、セリフ/歌詞の場合は <d></d> タグを使用してください。\n"
    return enhanced

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

# モードが切り替わった際にチャット履歴をリセット
if st.session_state.current_mode != mode:
    st.session_state.chat_history = []
    st.session_state.current_mode = mode

st.markdown("---")

# --------------------------------------------------------------------------------
# 3. 使い方・ガイドテキスト
# --------------------------------------------------------------------------------
with st.expander("📖 アプリの使い方と入力例（初めての方はこちらを開いてください）", expanded=True):
    if "FL2VA" in mode:
        st.markdown("""
### 💡 FL2VAモードの使い方 (T2V / I2V / FL2V)
画像を全く入れなければ「テキストからの動画生成(T2V)」になります。**「1stフレーム（開始）」**と**「Lastフレーム（終了）」**の最大2枚を指定すると、その間の変化やアクションを補間するプロンプトを生成します。

- **記入例（FL2V / 画像2枚）**：
  > 「`<Picture 1>`の閉じた宝箱が、5秒かけて光を放ちながら開き、`<Picture 2>`の輝く宝石で満たされた状態になる。激しい効果音とともにカメラが寄る。」
""")
    else:
        st.markdown("""
### 💡 Ref2VAモードの使い方
画像（最大9枚）・動画（最大3個）・音声（最大3個）を参照素材として指定し、キャラクターの顔、声質、BGMなどを固定・合成するプロンプトを生成します。

- **💡 重要（動画音声ピン `ref_video_audio_n` について）**：
  ComfyUIのMiniMax H3ノードには、動画ファイルに内包される音楽や音声を抽出して処理するための **`ref_video_audio_n`** ピンが用意されています。動画の音声をそのまま活かす場合は、動画ファイルとあわせて音声トラックも正しく結線してください。

- **記入例（概要欄）**：
  > 「`<Picture 1>`の女性が、`<Video 1>`の音楽と歌に合わせてリップシンクし、リズムよく踊るミュージックビデオ。映像は使用せず、`<Video 1>`の音声トラックを全編に使用する。」
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
    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        st.error(f"APIクライアントの初期化に失敗しました: {e}")
        st.stop()

    # ============================================================================
    # A. FL2VA モードの処理
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
        st.subheader("2. 演出設定と動画のストーリー")
        
        # 追加機能UIの描画
        video_style, use_text, overlay_text, text_style = render_style_and_text_options()
        
        user_summary = st.text_area(
            "作りたい動画の展開、または1stからLastへ向けての変化・アクションを入力してください（日本語OK）",
            placeholder="例：<Picture 1>のキャラクターが剣を振り下ろし、爆発とともに<Picture 2>のポーズに変化する。",
        )

        if st.button("🚀 FL2VAプロンプトを自動生成"):
            if not first_frame and not last_frame and not user_summary.strip():
                st.warning("テキスト概要、または画像を入力してください。")
            else:
                base_rules = load_rule_file("base-en.md")
                st.session_state.chat_history = []  # 履歴リセット

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

                        enhanced_user_summary = build_enhanced_summary(user_summary, video_style, use_text, overlay_text, text_style)

                        system_prompt = f"""
あなたはMiniMax H3 FL2VA (First-and-Last-Frame Mode) 専門プロンプト生成AIです。
提供されたキーフレーム画像情報と指示、および以下の【公式プロンプトルール】に完全準拠して、FL2VA公式規格(3ブロック構造)の英語プロンプトと和訳を出力してください。
推測やルールにない独自のフォーマットは使用しないでください。

【公式プロンプトルール (base-en.md)】
{base_rules}

【フレーム情報】
{frame_info if frame_info else "テキストのみのT2V生成"}

{enhanced_user_summary}

【出力フォーマット】
以下の3つのブロックのみを出力してください（subject_definitions や retention_analysis は絶対に使用しないでください）。

===ENGLISH_PROMPT===
integrated_multimodal_description:
<ルールに従い記述>

overall_soundscape:
<ルールに従い記述>

non_diegetic_music:
<ルールに従い記述>

===JAPANESE_TRANSLATION===
【日本語訳・解説】
<セクションごとの和訳>
"""
                        prompt_inputs.append(system_prompt)
                        user_content = build_user_content(prompt_inputs)
                        
                        # API呼び出しと履歴保存
                        response = call_gemini(client, [user_content])
                        st.session_state.chat_history = [user_content, response.candidates[0].content]

                    except Exception as e:
                        st.error(f"エラーが発生しました: {e}")

    # ============================================================================
    # B. Ref2VA モードの処理
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

        # バリデーション
        validation_errors = []
        if num_imgs > MAX_IMGS:
            validation_errors.append(f"画像は最大{MAX_IMGS}枚までです（現在: {num_imgs}枚）。")
        if num_vids > MAX_VIDS:
            validation_errors.append(f"動画は最大{MAX_VIDS}個までです（現在: {num_vids}個）。")
        if num_auds > MAX_AUDS:
            validation_errors.append(f"音声は最大{MAX_AUDS}個までです（現在: {num_auds}個）。")
        if total_files > MAX_TOTAL:
            validation_errors.append(f"ファイル合計が{MAX_TOTAL}個を超えています（現在: {total_files}個）。")
        if num_auds > 0 and num_imgs == 0 and num_vids == 0:
            validation_errors.append("音声ファイル単体では使用できません。必ず画像または動画と一緒にアップロードしてください。")

        if validation_errors:
            for err in validation_errors:
                st.error(f"⚠️ {err}")
        else:
            file_instructions = []
            if total_files > 0:
                st.markdown("---")
                st.subheader("2. 各ファイルの「役割」と「使用部分」を指定")
                
                # 画像のマルチセレクト
                if img_files:
                    st.markdown("#### 🖼️ 画像のガイド")
                    for i, file in enumerate(img_files, 1):
                        c1, c2 = st.columns([1, 2])
                        with c1:
                            roles = st.multiselect(
                                f"<Picture {i}> の役割",
                                ["人物・身元", "衣装・小道具", "背景・環境", "構図", "スタイル"],
                                default=[],
                                key=f"r_img_{i}",
                            )
                        with c2:
                            detail = st.text_input(
                                f"<Picture {i}> のどの部分を使うか？",
                                placeholder="例：背景と人物を分けて別々のSubjectにして",
                                key=f"r_img_d_{i}",
                            )
                        role_str = "、".join(roles) if roles else "全体"
                        file_instructions.append(
                            f"- <Picture {i}>: 役割=[{role_str}], 指示=[{detail if detail else '全体'}]"
                        )

                # 動画のマルチセレクト
                if vid_files:
                    st.markdown("#### 🎥 動画のガイド（※動画音声は ref_video_audio_n ピンに結線します）")
                    for i, file in enumerate(vid_files, 1):
                        c1, c2 = st.columns([1, 2])
                        with c1:
                            roles = st.multiselect(
                                f"<Video {i}> の役割",
                                ["ダンス・アクション", "カメラワーク", "全体構造", "音声・音楽トラック (ref_video_audio)"],
                                default=[],
                                key=f"r_vid_{i}",
                            )
                        with c2:
                            detail = st.text_input(
                                f"<Video {i}> のどの部分を使うか？",
                                placeholder="例：モーションと音声トラックを使用",
                                key=f"r_vid_d_{i}",
                            )
                        role_str = "、".join(roles) if roles else "全体"
                        file_instructions.append(
                            f"- <Video {i}>: 役割=[{role_str}], 指示=[{detail if detail else '全体の動きと音声'}]"
                        )

                # 音声のマルチセレクト
                if aud_files:
                    st.markdown("#### 🎵 音声のガイド")
                    for i, file in enumerate(aud_files, 1):
                        c1, c2 = st.columns([1, 2])
                        with c1:
                            roles = st.multiselect(
                                f"<Audio {i}> の役割",
                                ["声質（クローン）", "リップシンク用ボーカル", "BGM", "効果音"],
                                default=[],
                                key=f"r_aud_{i}",
                            )
                        with c2:
                            detail = st.text_input(
                                f"<Audio {i}> のどの部分を使うか？",
                                placeholder="例：声質だけ、テンポだけ",
                                key=f"r_aud_d_{i}",
                            )
                        role_str = "、".join(roles) if roles else "全体"
                        file_instructions.append(
                            f"- <Audio {i}>: 役割=[{role_str}], 指示=[{detail if detail else '全体の音'}]"
                        )

            st.markdown("---")
            st.subheader("3. 演出設定と動画の全体ストーリー")
            
            # 追加機能UIの描画
            video_style, use_text, overlay_text, text_style = render_style_and_text_options()

            user_summary = st.text_area(
                "動画のストーリーやセリフ（日本語OK）",
                placeholder="例：<Picture 1>の女性が<Video 1>の音楽と歌に合わせて踊る。映像は使わない。",
            )

            if st.button("🚀 Ref2VAプロンプトを自動生成"):
                if total_files == 0 and not user_summary.strip():
                    st.warning("参照ファイル、または動画概要のどちらかを入力してください。")
                else:
                    base_rules = load_rule_file("base-en.md")
                    ref_rules = load_rule_file("ref-en.md")
                    st.session_state.chat_history = []  # 履歴リセット

                    with st.spinner("Ref2VA公式規格（6セクション構造）へ変換中..."):
                        prompt_inputs = []
                        temp_files = []
                        uploaded_files = []

                        try:
                            if img_files:
                                for f in img_files:
                                    prompt_inputs.append(
                                        types.Part.from_bytes(
                                            data=f.getvalue(),
                                            mime_type=get_mime_type(f),
                                        )
                                    )

                            if vid_files:
                                for f in vid_files:
                                    gfile, tmp_path = upload_via_file_api(client, f)
                                    temp_files.append(tmp_path)
                                    uploaded_files.append(gfile)
                                    prompt_inputs.append(gfile)

                            if aud_files:
                                for f in aud_files:
                                    gfile, tmp_path = upload_via_file_api(client, f)
                                    temp_files.append(tmp_path)
                                    uploaded_files.append(gfile)
                                    prompt_inputs.append(gfile)

                            for idx, u_file in enumerate(uploaded_files):
                                uploaded_files[idx] = wait_for_file_active(client, u_file)

                            formatted_instructions = "\n".join(file_instructions)
                            enhanced_user_summary = build_enhanced_summary(user_summary, video_style, use_text, overlay_text, text_style)
                            
                            system_prompt = f"""
あなたはMiniMax H3 Ref2VA(Omni-Reference Mode)専用のプロンプト生成AIです。
以下の素材指示と動画概要、および【公式プロンプトルール】に完全準拠し、Ref2VA公式規格(6セクション構造)の英語プロンプトと和訳を出力してください。
ルールにない独自のフォーマット、推測による補完、勝手な要素の付け足しは一切行わないでください。

【絶対遵守のコア原則（温度0.0準拠）】
1. 外見描写の絶対的禁止（No Visual Descriptions）
参照素材（画像・動画）の見た目（服装、髪型、装飾品、背景の詳細など）をプロンプト内で絶対に言語化しないでください。視覚的特徴の解析はMiniMaxのビジョンエンコーダーが行います。対象を指す際は「the character」「the background environment」といった最短の識別子のみを使用してください。
2. 変数化と反復説明の禁止（DRY Principle）
`subject_definitions` で定義した `<Subject N>` や `<Audio N>` はプログラムにおける変数です。以降のセクションで呼び出す際は、必ずラベル（例: `<Subject 1>`) のみを使用し、それが何であるかの再説明・再描写を一切行わないでください。
3. 動画音声トラック（ref_video_audio）の適切な定義
動画ファイル（`<Video N>`）の音声や音楽トラックを利用する指示がある場合、それは専用の音声参照（ref_video_audio）として結線されるため、対応するオーディオ変数として定義し、サウンドトラックセクションで正しく適用してください。
4. セリフ・歌詞の捏造の絶対禁止（No Hallucination of Dialogue）
ユーザーが具体的な歌詞やセリフのテキストを明示していない場合、AIが勝手に内容を創作して `<d>[言語] セリフ </d>` のようなリップシンク用タグを使用することを固く禁じます。「音楽と歌に合わせて歌って踊る」「リップシンクする」といった動作の指示のみがある場合は、テキストを捏造せず、単に「singing in lip-sync and dancing in rhythm to <Video N>`s audio track」のように動作としてのみ描写してください。

【公式プロンプトルール (base-en.md)】
{base_rules}

【Ref2VA 専用追加ルール (ref-en.md)】
{ref_rules}

【素材指示】
{formatted_instructions}

{enhanced_user_summary}

【出力フォーマット】
以下の構成に完全に一致させて出力してください。余分な説明や挨拶は一切不要です。

===ENGLISH_PROMPT===
subject_definitions:
<Subject 1> is the character whose visual identity and appearance are defined by <Picture 1>.
<Subject 2> is the motion structure and rhythm defined by <Video 1>.
<Audio 1> is the music and vocal track extracted from <Video 1>, which is fully reused in the target video.

summary:
[reference generation + audio reuse] The target video features <Subject 1> performing a music video where she sings in lip-sync and dances in rhythm to <Audio 1> throughout the entire duration.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - the appearance and features of the character from <Picture 1> are fully retained.
<Subject 2> (appears in [Shot 1]): reference - the motion and dance timing from <Video 1> guide the performance.
<Audio 1>: fully_copy - the complete music and vocal track from <Video 1> serves as the target video's complete final audio track via ref_video_audio.

detailed_description:
The target video uses a cinematic style.
[Shot 1] The shot opens referencing <Picture 1>. <Subject 1> (S1) performs dance choreography in time with the rhythm of <Audio 1>, singing in lip-sync throughout the continuous shot. The camera maintains a stable framing.

overall_soundscape:
N/A

non_diegetic_music:
<Audio 1> is directly reused as the complete final audio track and rhythm source.

===JAPANESE_TRANSLATION===
【日本語訳・解説】
<出力した英語プロンプトのセクションごとの和訳>
"""
                            prompt_inputs.append(system_prompt)
                            user_content = build_user_content(prompt_inputs)
                            
                            # API呼び出しと履歴保存
                            response = call_gemini(client, [user_content])
                            st.session_state.chat_history = [user_content, response.candidates[0].content]

                        except Exception as e:
                            st.error(f"エラーが発生しました: {e}")

                        finally:
                            cleanup_temp_files(temp_files)

    # ============================================================================
    # C. 対話的リファイン（チャット修正機能）と結果表示
    # ============================================================================
    if st.session_state.chat_history:
        # 常に最新の生成結果を表示する
        latest_text = st.session_state.chat_history[-1].parts[0].text
        render_result(latest_text)
        
        # 結果表示の下に修正用のチャット入力欄を配置
        user_msg = st.chat_input("プロンプトの修正・調整指示を入力してください（例：背景と人物を分けて再定義して）")
        if user_msg:
            # ユーザーの追加指示を履歴に保存
            st.session_state.chat_history.append(
                types.Content(role="user", parts=[types.Part.from_text(text=user_msg)])
            )
            with st.spinner("AIがプロンプトを再調整しています..."):
                try:
                    # コンテキスト（画像・ルール・直前の回答）を保持したまま再生成
                    response = call_gemini(client, st.session_state.chat_history)
                    st.session_state.chat_history.append(response.candidates[0].content)
                    st.rerun()  # 画面を更新して最新結果を表示
                except Exception as e:
                    st.error(f"修正中にエラーが発生しました: {e}")
                    st.session_state.chat_history.pop()  # エラー時は失敗した指示を履歴から取り消す

else:
    st.info("👈 左側のサイドバーに Gemini API Key を入力してください。")
