import streamlit as st
import google.generativeai as genai
from PIL import Image

# 1. 画面の基本設定
st.title("MiniMax H3 (Ref2VA) Prompt Generator")
st.caption("参照画像と動画概要を入れるだけで、Ref2VA規格のプロンプトを自動生成します。")

# APIキー設定（StreamlitのSecrets管理またはサイドバーから入力）
api_key = st.sidebar.text_input("Gemini API Key", type="password")

if api_key:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-3-flash-preview")

    # 2. UI要素：画像アップロード（最大9枚）
    uploaded_files = st.file_uploader(
        "参照画像（最大9枚）をアップロード", 
        type=["png", "jpg", "jpeg", "webp"], 
        accept_multiple_files=True
    )

    # 3. UI要素：概要入力
    user_summary = st.text_area(
        "動画のイメージ・概要を入力（日本語OK）",
        placeholder="例：画像1のキャラクターが、画像2の背景でカメラに向かって手を振りながら明るく挨拶する。"
    )

    # 生成ボタン
    if st.button("Ref2VAプロンプトを生成"):
        if not user_summary:
            st.warning("動画の概要を入力してください。")
        else:
            with st.spinner("プロンプトを生成中..."):
                images = []
                image_ref_text = ""
                
                if uploaded_files:
                    for idx, file in enumerate(uploaded_files, start=1):
                        img = Image.open(file)
                        images.append(img)
                        image_ref_text += f"- <Picture {idx}>: アップロードされた画像 {idx}\n"

                # システムプロンプト（Ref2VA 6セクション構造の強制）
                system_prompt = f"""
あなたはMiniMax H3のRef2VA（Omni-Reference Mode）用プロンプトの専門生成AIです。
ユーザーから提供された画像および動画の概要テキストをもとに、公式規格に完全準拠した英語プロンプトを生成してください。

【参照情報の整理】
{image_ref_text if image_ref_text else "参照画像なし"}

【必須出力フォーマット】
以下の6つのセクションのみを小文字ラベルで正確に出力してください（説明文や余計な解説は一切含めないでください）。

subject_definitions:
<定義文>

summary:
<全体概要と「Hard cuts between shots, no dissolves.」等の記述>

retention_analysis:
<各参照素材の保持分析(fully_preserved 等)>

detailed_description:
<画質・照明・各ショット([Shot 1]等)の詳細なカメラワークや動き。セリフは <d>[Japanese] 「...」</d> タグを使用>

overall_soundscape:
<環境音・効果音・セリフ音声の指定>

non_diegetic_music:
<BGM指定>

【ユーザーの動画概要】
{user_summary}
"""

                # Geminiへのリクエスト送信（画像 + テキスト）
                prompt_inputs = [system_prompt] + images
                response = model.generate_content(prompt_inputs)

                # 4. 結果表示
                st.subheader("生成されたRef2VAプロンプト")
                st.code(response.text, language="text")

else:
    st.info("サイドバーにGemini API Keyを入力してください。")
