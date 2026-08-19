import streamlit as st
import google.generativeai as genai
from PIL import Image

# --------------------------------------------------------------------------------
# 1. ページ基本設定
# --------------------------------------------------------------------------------
st.set_page_config(
    page_title="MiniMax H3 (Ref2VA) Prompt Generator",
    page_icon="🎬",
    layout="wide"
)

# --------------------------------------------------------------------------------
# 2. 画面冒頭の使い方・ガイドテキスト（ライトユーザー向け）
# --------------------------------------------------------------------------------
st.title("🎬 MiniMax H3 (Ref2VA) オムニ参照プロンプトメーカー")
st.caption("手持ちの素材（画像・動画・音声）と日本語の指示から、MiniMax H3公式規格の6セクション英語プロンプトおよび和訳を自動生成します。")

with st.expander("📖 アプリの使い方と入力例（初めての方はこちらを開いてください）", expanded=True):
    st.markdown("""
### 💡 このアプリの使い方（簡単3ステップ）

**【手順 1】参照ファイルをアップロードする（任意 / 合計12個まで）**
使いたい素材（画像最大9枚 / 動画最大3個 / 音声最大3個）を選択します。
※画像や素材がなくても、テキストのみでプロンプトを作成することも可能です！

**【手順 2】各ファイルの「役割」と「使う部分」を指定する（ガイド機能）**
素材をアップロードすると画面に質問が表示されます。AIに以下の2点を教えてください：
- **このファイルの役割**：例「キャラクターの顔」「BGM」「動きのモーション」など
- **どの部分を使うか？**：例「顔と髪型だけ」「背景の雰囲気」「声質だけ」など

**【手順 3】動画の全体ストーリー・概要を入力して生成！**
一番下のテキストエリアに、作りたい動画の展開やセリフを入力し「生成ボタン」を押してください。

---

### ✍️ 「動画の概要」には何を書けばいいの？（記入例）

日本語で自由に書いてOKです！素材タグ（`<Picture 1>`, `<Audio 1>` など）を混ぜて書くと、より正確に反映されます。

- **【記入例 1：画像と音声を使う場合】**
  > 「`<Picture 1>`の女の子が、`<Picture 2>`のカフェの席に座っている。`<Audio 1>`の声質で、カメラに向かって『一緒にカフェ巡りしよう！』と笑顔で喋る。」

- **【記入例 2：動画の動きを移植する場合】**
  > 「`<Picture 1>`のキャラクターが、`<Video 1>`のダンサーと同じ激しいダンスを完璧に踊る。背景はネオンが光るサイバーパンクな街並み。途中でカメラがアップになる。」
""")

# --------------------------------------------------------------------------------
# 3. サイドバー設定 (APIキー入力)
# --------------------------------------------------------------------------------
st.sidebar.header("🔑 APIキー設定")
api_key = st.sidebar.text_input("Gemini API Key を入力", type="password")
st.sidebar.markdown("""
<small>
※Gemini API KeyはGoogle AI Studio等から無料で入手できます。<br>
※入力されたキーはプロンプト生成のみに使用され、保存されません。
</small>
""", unsafe_allow_html=True)

# --------------------------------------------------------------------------------
# 4. メイン処理部
# --------------------------------------------------------------------------------
if api_key:
    genai.configure(api_key=api_key)
    # 最新モデルを指定
    model = genai.GenerativeModel("gemini-3-flash-preview")

    st.subheader("1. 参照ファイルのアップロード（合計最大12個まで）")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        img_files = st.file_uploader("🖼️ 画像（最大9枚）", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True)
    with col2:
        vid_files = st.file_uploader("🎥 動画（最大3個 / 各2~15秒）", type=["mp4", "mov"], accept_multiple_files=True)
    with col3:
        aud_files = st.file_uploader("🎵 音声（最大3個 / 各2~15秒）", type=["wav", "mp3", "m4a", "aac", "ogg", "flac"], accept_multiple_files=True)

    num_imgs = len(img_files) if img_files else 0
    num_vids = len(vid_files) if vid_files else 0
    num_auds = len(aud_files) if aud_files else 0
    total_files = num_imgs + num_vids + num_auds

    # バリデーションチェック
    if total_files > 12:
        st.error(f"⚠️ ファイルの合計数が12個を超えています（現在: {total_files}個）。合計12個以下に調整してください。")
    elif num_auds > 0 and (num_imgs == 0 and num_vids == 0):
        st.warning("⚠️ MiniMax H3の仕様上、音声ファイル単体では使用できません。必ず画像または動画と一緒にアップロードしてください。")
    else:
        file_instructions = []
        
        # ファイルごとの個別ガイド・フォーム表示
        if total_files > 0:
            st.markdown("---")
            st.subheader("2. 各ファイルの「役割」と「使用部分」を指定（ガイド機能）")
            st.info("💡 以下の各ファイルについて「何の目的で」「どの要素を使うか」をドロップダウンとテキストで教えてください。")

            # 画像ガイド
            if img_files:
                st.markdown("#### 🖼️ 画像のガイド")
                for i, file in enumerate(img_files, 1):
                    c1, c2 = st.columns([1, 2])
                    with c1:
                        role = st.selectbox(
                            f"<Picture {i}> ({file.name}) の役割", 
                            ["人物・キャラクターの身元", "衣装・小道具", "背景・環境", "構図・絵コンテ", "全体スタイル"], 
                            key=f"img_role_{i}"
                        )
                    with c2:
                        detail = st.text_input(
                            f"<Picture {i}> のどの部分を使うか？", 
                            placeholder="例：顔と髪型だけ、服装だけ、背景の雰囲気全体 など", 
                            key=f"img_detail_{i}"
                        )
                    file_instructions.append(f"- <Picture {i}> ({file.name}): 役割=[{role}], 使用部分・指示=[{detail if detail else '全体を使用'}]")

            # 動画ガイド
            if vid_files:
                st.markdown("#### 🎥 動画のガイド")
                for i, file in enumerate(vid_files, 1):
                    c1, c2 = st.columns([1, 2])
                    with c1:
                        role = st.selectbox(
                            f"<Video {i}> ({file.name}) の役割", 
                            ["ダンス・体のアクション", "カメラワーク", "動画の全体構造・編集", "背景の動き"], 
                            key=f"vid_role_{i}"
                        )
                    with c2:
                        detail = st.text_input(
                            f"<Video {i}> のどの部分を使うか？", 
                            placeholder="例：人物のダンスモーションだけ、カメラの引きの動きだけ など", 
                            key=f"vid_detail_{i}"
                        )
                    file_instructions.append(f"- <Video {i}> ({file.name}): 役割=[{role}], 使用部分・指示=[{detail if detail else '全体の動きを使用'}]")

            # 音声ガイド
            if aud_files:
                st.markdown("#### 🎵 音声のガイド")
                for i, file in enumerate(aud_files, 1):
                    c1, c2 = st.columns([1, 2])
                    with c1:
                        role = st.selectbox(
                            f"<Audio {i}> ({file.name}) の役割", 
                            ["キャラクターの声質（クローン）", "リップシンク用のボーカル", "BGM（劇伴）", "効果音・環境音"], 
                            key=f"aud_role_{i}"
                        )
                    with c2:
                        detail = st.text_input(
                            f"<Audio {i}> のどの部分を使うか？", 
                            placeholder="例：声質だけ似せる、テンポとリズムだけ参照、曲をそのまま流す など", 
                            key=f"aud_detail_{i}"
                        )
                    file_instructions.append(f"- <Audio {i}> ({file.name}): 役割=[{role}], 使用部分・指示=[{detail if detail else '全体の音を使用'}]")

        st.markdown("---")
        st.subheader("3. 動画の全体ストーリー・概要")
        user_summary = st.text_area(
            "作りたい動画のストーリーやセリフを入力してください（日本語OK）",
            placeholder="例：<Picture 1>の女の子が、<Audio 1>の曲に合わせて「こんにちは！」と笑顔で挨拶しながら踊る。"
        )

        # 生成実行
        if st.button("🚀 Ref2VAプロンプトを自動生成"):
            if not user_summary and total_files == 0:
                st.warning("ファイルまたは動画概要を入力してください。")
            else:
                with st.spinner("Ref2VA規格プロンプト＆和訳を生成中..."):
                    images_for_api = []
                    if img_files:
                        for f in img_files:
                            images_for_api.append(Image.open(f))

                    formatted_instructions = "\n".join(file_instructions)

                    # H3公式仕様(6セクション構造)＋和訳出力を強制するシステムプロンプト
                    system_prompt = f"""
あなたはMiniMax H3のRef2VA（Omni-Reference Mode）用プロンプトの専門生成AIです。
ユーザーから提供された参照ファイル情報と動画概要をもとに、公式規格に完全準拠した英語プロンプトおよびその日本語訳を生成してください。

【ユーザーが指定した素材使用ガイド】
{formatted_instructions if formatted_instructions else "参照ファイルなし"}

【必須出力フォーマット】
以下の区切り線タグおよび6つの小文字セクションラベルを正確に使用して出力してください。

===ENGLISH_PROMPT===
subject_definitions:
<各素材タグ(<Picture N>, <Video N>, <Audio N>)とSubjectの役割定義文>

summary:
<全体概要と「hard cuts between shots, no dissolves.」等の編集指示記述>

retention_analysis:
<各素材の保持分析 (fully_preserved, reference, fully_copy 等の表記)>

detailed_description:
<画質・照明・各ショット([Shot 1]等)の詳細構成。セリフがある場合は <d>[Japanese] 「...」</d> タグを使用>

overall_soundscape:
<環境音・効果音・物理音・セリフ音声の指定>

non_diegetic_music:
<BGM指定。冒頭から鳴らす場合は「From the very first frame...」等を記載>

===JAPANESE_TRANSLATION===
【日本語訳・解説】
（上記で生成した英語プロンプトの内容を、セクションごとに分かりやすく日本語で翻訳・解説してください）

【ユーザーの全体ストーリー概要】
{user_summary}
"""

                    prompt_inputs = [system_prompt] + images_for_api
                    response = model.generate_content(prompt_inputs)

                    # 出力結果の分割と表示（タブ形式）
                    st.markdown("---")
                    st.subheader("✨ 生成結果")

                    if "===JAPANESE_TRANSLATION===" in response.text:
                        parts = response.text.split("===JAPANESE_TRANSLATION===")
                        english_text = parts[0].replace("===ENGLISH_PROMPT===", "").strip()
                        japanese_text = parts[1].strip()

                        tab_en, tab_ja = st.tabs(["📋 生成プロンプト (英語)", "🇯🇵 日本語訳"])
                        
                        with tab_en:
                            st.info("💡 以下のコードブロック右上のアイコンからワンタップでコピーし、ComfyUIやH3のプロンプト欄へ貼り付けてください。")
                            st.code(english_text, language="text")
                            
                        with tab_ja:
                            st.markdown(japanese_text)
                    else:
                        st.code(response.text, language="text")

else:
    st.info("👈 左側のサイドバーに Gemini API Key を入力してください。")
