# VoiceStudio: отдельный локальный CPU модуль

Это **настоящий VoiceStudio 0.5.7**, upstream `debpalash/VoiceStudio` на commit `fc154e767227f46f2297621b854bed8685e27247`, а не имитация его функций в чате. На текущем сервере установлен один русский голос Piper/Ирина через штатный Sherpa-ONNX. Клонирование, voice design, ASR и нейродубляж не установлены и не объявляются доступными.

## Лицензия и исходники

VoiceStudio распространяется по **GNU AGPL-3.0-only**; полный текст в [LICENSE](LICENSE). Эти отдельные адаптер, hosted bootstrap, service template и installer распространяются на тех же условиях. Изменения ограничены дополнительными файлами этого каталога и `install.py`; upstream bundles/движки не патчились.

Полный исходный код работающего VoiceStudio: https://github.com/debpalash/VoiceStudio/tree/fc154e767227f46f2297621b854bed8685e27247 . Исходники всех локальных изменений находятся рядом с этим README в публичном форке; для воспроизведения checkout указанного upstream и применить открытый installer/профиль. Предложение исходного кода относится к VoiceStudio и этому отдельному модулю, а не меняет лицензию основной Open WebUI. При собственной публичной выкладке сохраните ссылку на точный upstream и свои изменённые исходники.

Веса и пользовательские данные **не включены**. Архив Piper загружается отдельно с официального Sherpa release (URL/SHA в `upstream.json`). Его MODEL_CARD указывает Dataset License: **Unknown**; это факт о выбранной модели, не разрешение её перераспространять. Не включать эти веса в публичный форк/прототип или коммерческий пакет без проверки модели. Файл лицензии runtime Sherpa не заменяет лицензию голоса.

## Воспроизведение текущей установки

Ниже проверенная схема для Linux/Python3.12 с уже установленным совместимым Torch и core-зависимостями в отдельных стабильных окружениях. Общие окружения только читаются, `pip` их не меняет. **Это не обещание проверенного чистого bootstrap на произвольном компьютере.** Для чистого хоста сначала установить отдельный совместимый Torch/core environment по upstream-инструкции с учётом доступной памяти.

```sh
git clone https://github.com/debpalash/VoiceStudio.git "$HOME/.local/share/voicestudio-src"
git -C "$HOME/.local/share/voicestudio-src" checkout fc154e767227f46f2297621b854bed8685e27247
python3 -m venv "$HOME/.local/share/voicestudio-venv"
```

В собственный `voicestudio-venv/lib/python3.12/site-packages/local-core-readonly.pth` добавить **реальные** стабильные пути уже проверенных Torch/core site-packages. Пути выбирает оператор своего хоста; чужие runtime пути не включены. Никогда не копировать `.env`, токены, browser profiles или credentials.

```sh
"$HOME/.local/share/voicestudio-venv/bin/python" -m pip install -r custom/deploy/voicestudio/requirements-local.lock
mkdir -p "$HOME/.local/share/voicestudio-data/models"
curl -fLsS https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-ru_RU-irina-medium.tar.bz2 -o /tmp/irina.tar.bz2
sha256sum /tmp/irina.tar.bz2
# Expected: 1fc0f54e5e084fe287c07909f2f6e0ba6d857864cf800e3ab80286a4e8233008
# Check the archive member paths, then extract into the dedicated models dir.
```

Безопасное извлечение архива через Python `tarfile.extractall(..., filter='data')` использовано в установленном варианте. Ожидается каталог `vits-piper-ru_RU-irina-medium` с ONNX, tokens.txt, espeak data и MODEL_CARD.

Установить **Bun1.4.2 отдельно для этого модуля** из официального `oven-sh/bun` release; не заменять системный Node/npm. Сохранить upstream `bun.lock`:

```sh
cd "$HOME/.local/share/voicestudio-src"
bun install --frozen-lockfile --ignore-scripts
NODE_OPTIONS=--max-old-space-size=900 bun run build:web
```

Сборки на общем сервере запускать последовательно под CPU100/MemoryMax1200M. Web build создаёт `frontend/dist`. Затем из checkout репозитория интеграции:

```sh
python3 custom/deploy/voicestudio/install.py --origin https://YOUR-HUB:8473
systemctl --user status hub-voicestudio
curl http://127.0.0.1:8771/health
```

Installer создаёт только свою user unit/config/wrapper, включает службу, проверяет localhost API и при отсутствии preset создаёт образец настоящим localTTS. Hosted bootstrap выбирает его для нового браузера; старые черновики не стираются. `Linger=yes` нужен для автозапуска после reboot без входа пользователя. На текущем сервере уже включён.

## Границы и проверка

- Native **127.0.0.1:8771**; внешний доступ только через owner-auth TLS gateway **8473**, который защищает **каждый** маршрут, включая assets/audio/WebSocket/MCP. Native listener в LAN не открывать.
- CPU100%, MemoryMax1200M; без GPU, ASR/watermark preloads и аналитики; HF/Transformers offline. Allowlist и доступность двигателей — в `local_entry.py` и runtime environment. Не включать тяжёлые engines без теста RAM/GPU.
- Настоящий русский UI синтез: новое окно → Piper preset → русский текст → Synthesize audio → WAV/проигрывание → **Скачать WAV**. Штатный Electron Save as в браузере не используется.
- Проверено: backend health, 10 guard tests, mobile390 UI/real speech/audio playback; защищённый gateway выполняет те же действия и реальное скачивание. Остальные функции upstream проверять отдельно; наличие их пунктов не означает установку нужных моделей.
- Подробнее запуск/откат: [защищённые шлюзы и отключение](../../LOCAL-MODULES.md).
