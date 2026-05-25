import os
import time
from nicegui import app, ui

# 実行ファイルと同じディレクトリのパス
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

# 記録用テキストエリアの参照を保持する変数
text_area = None
video_element = None

def init_ui():
    global text_area, video_element
    
    ui.page_title('動画タイムスタンプ記録 Webアプリ')
    ui.markdown('### 🎬 動画タイムスタンプ記録（ブラウザ版）')
    
    # 静的ファイルとして現在のディレクトリを通す
    app.add_static_files('/static', CURRENT_DIR)
    
    with ui.row().classes('w-full no-wrap'):
        # 左側：動画表示エリア
        with ui.card().classes('w-2/3 p-4'):
            ui.label('① 下のボタンから別の動画ファイル(mp4)を選択することもできます')
            
            # ファイルアップロード処理
            def handle_upload(e):
                file_name = e.name
                unique_name = f"temp_video_{int(time.time())}.mp4"
                file_path = os.path.join(CURRENT_DIR, unique_name)
                
                # 古いテンポラリファイルを削除
                for f in os.listdir(CURRENT_DIR):
                    if f.startswith("temp_video_") and f.endswith(".mp4"):
                        try:
                            os.remove(os.path.join(CURRENT_DIR, f))
                        except:
                            pass
                
                with open(file_path, 'wb') as f:
                    f.write(e.content.read())
                
                video_element.source = f'/static/{unique_name}'
                ui.run_javascript(f"document.getElementById('my-video').load();")
                ui.notify(f'「{file_name}」を読み込みました。')

            ui.upload(on_upload=handle_upload, max_files=1, label="mp4ファイルをアップロード").classes('w-full')
            
            # デフォルトの動画ファイルを定義
            default_video_name = "dogsample.mp4"
            default_video_path = os.path.join(CURRENT_DIR, default_video_name)
            
            initial_source = ''
            if os.path.exists(default_video_path):
                initial_source = f'/static/{default_video_name}?t={int(time.time())}'
                ui.notify(f'デフォルトの動画「{default_video_name}」を読み込みました。')
            else:
                ui.notify(f'警告: {default_video_name} が同じディレクトリに見つかりません。', type='warning')

            video_element = ui.video(initial_source).classes('w-full')
            video_element.props('id="my-video" controls')

        # 右側：記録エリア
        with ui.card().classes('w-1/3 p-4'):
            ui.label('【記録ログ】')
            text_area = ui.textarea(placeholder='「R」キーを押すとここに時間が記録されます').classes('w-full h-80')
            
            def save_to_file():
                save_path = os.path.join(CURRENT_DIR, "record.txt")
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write(text_area.value)
                ui.notify(f'record.txt に保存しました！', type='positive')
                
            ui.button('record.txt に保存', on_click=save_to_file).classes('w-full q-mt-md')

    # --- JavaScriptのイベントキャッチを強化 ---
    ui.add_head_html('''
    <script>
    // 時間をフォーマットしてテキストエリアに追記する共通関数
    function recordVideoTime(video) {
        if (!video) return;
        const currentTimeSec = video.currentTime;
        const totalMs = Math.floor(currentTimeSec * 1000);
        const hrs = String(Math.floor(totalMs / 3600000)).padStart(2, '0');
        const mins = String(Math.floor((totalMs % 3600000) / 60000)).padStart(2, '0');
        const secs = String(Math.floor((totalMs % 60000) / 1000)).padStart(2, '0');
        const ms = String(totalMs % 1000).padStart(3, '0');
        
        const timeStr = `${hrs}:${mins}:${secs},${ms}`;
        
        const textArea = document.querySelector('.q-textarea textarea');
        if (textArea) {
            if (textArea.value) {
                textArea.value += '\\n' + timeStr;
            } else {
                textArea.value = timeStr;
            }
            textArea.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }

    // 1. ページ全体（document）でのキー監視
    document.addEventListener('keydown', function(event) {
        if ((event.key === 'r' || event.key === 'R') && event.target.tagName !== 'TEXTAREA' && event.target.tagName !== 'INPUT') {
            event.preventDefault();
            const video = document.getElementById('my-video');
            recordVideoTime(video);
        }
    });

    // 2. 動画プレイヤー要素（video）がアクティブな時のキー監視（強化ポイント）
    // 定期的にvideo要素のロードを監視し、イベントリスナーを直接バインドします
    const initVideoInterval = setInterval(() => {
        const video = document.getElementById('my-video');
        if (video) {
            video.addEventListener('keydown', function(event) {
                // プレイヤーにフォーカスがある状態で R が押された場合
                if (event.key === 'r' || event.key === 'R') {
                    event.preventDefault();  // プレイヤー側の固有挙動をストップ
                    event.stopPropagation();  // イベントのバブリング（重複発生）をストップ
                    recordVideoTime(video);
                }
            });
            clearInterval(initVideoInterval); // バインドできたら監視を終了
        }
    }, 500);
    </script>
    ''')

init_ui()
ui.run(port=8080, title="Video Marker")