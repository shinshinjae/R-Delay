# 시연 영상 다시 만들기

실제 앱(docs/)을 무대(stage.html) 안에서 조작하며 녹화합니다. 화면이 바뀌면 다시 돌리세요.

```bash
# 저장소 맨 위에서
python3 -m http.server 8002 &                                # 저장소 맨 위에서: 무대(scripts/demo/stage.html)와 앱(docs/)을 같은 출처로
cd scripts/demo
node record.cjs frames 2                                     # 약 1분, 프레임을 frames/에 저장(배율 2)
python3 build_video.py                                       # frames/list.txt 작성
ffmpeg -y -f concat -safe 0 -i frames/list.txt -vf "scale=1920:1080:flags=lanczos,fps=30,format=yuv420p" \
  -c:v libx264 -preset veryfast -crf 18 -movflags +faststart R-Delay_demo.mp4
ffmpeg -y -i R-Delay_demo.mp4 -vf "fps=6,scale=1200:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=112:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" R-Delay_demo.gif   # README용(약 6MB) → image/demo.gif
```

- 장면 문구는 record.cjs의 `setCap(번호, 제목, 설명)`에서 바꿉니다. 제목의 `<em>`은 주황 강조입니다.
- Playwright(Chromium)와 ffmpeg가 필요합니다. frames/는 커밋하지 마세요(.gitignore).
- 무대의 주소 표시(stage.html)와 끝 화면의 `qr.png`는 공개 주소 https://shinshinjae.github.io/R-Delay/ 를 가리킵니다(`qr.png`는 새 주소로 다시 만듦).
- README의 `image/demo.gif`는 예전 주소를 쓰던 때(2026-11-05) 녹화해 화면 속 주소창에 예전 주소가 보입니다. 끝 화면의 QR만 새 주소로 바꿔 넣었습니다. 다시 녹화하면 주소창도 새 주소로 나옵니다.
