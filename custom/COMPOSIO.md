# Composio в Sasha AI Workbench

Интеграция встроена в общий слой «Плагины и навыки → Сервисы», а не только в Claude Code. Отдельный SDK/фоновая служба не нужны: адаптер использует официальный HTTPS REST API Composio v3.1 через существующий httpx.

## Подключить

1. Откройте карточку **Composio · приложения для всех моделей** → «Добавить подключение».
2. Введите **project API key** из https://dashboard.composio.dev и явный список приложений, например `gmail,slack,github`. Ключ с ограниченными правами должен разрешать Session management и Session tool execution. Не используйте организационный user API key.
3. Оставьте `read`; отдельно включите `connect` для входа в приложения и `write` только для подтверждаемых вызовов. Сохраните и нажмите «Проверить».
4. Проверка создаёт личную сессию с псевдонимом, полученным от аутентифицированного владельца и ID подключения. Статус `connected` означает доступность **Composio API**, а не авторизацию всех перечисленных приложений.
5. «Инструменты» → `connect` → `{"toolkit":"gmail"}` → «Подтвердить действие» → «Войти в выбранное приложение». Проверьте аккаунт и scopes на стороне приложения. Остальные приложения подключаются отдельно.
При истёкшей или удалённой сессии нажмите **«Пересоздать сессию»**. Это сохраняет личный Composio user ID и подключённые к нему аккаунты; старые действия не повторяются. Удалённая предыдущая сессия не отзывается автоматически.

6. `list` возвращает каталог инструментов/схем и `next_cursor`. Для следующей страницы передайте его в `cursor`.

Ключ остаётся в закрытом owner state сервера. Он не сохраняется в localStorage, не передаётся модели и не экспортируется в публичный форк. Модель не может задать Composio user ID, session ID или API URL.

## Модели и действия

Codex и Claude получают через общий connector MCP `list` и `read`. В `read` разрешены только явно перечисленные в `chat_composio.READ_TOOLS` операции Gmail/Slack/GitHub/Google Calendar. Их чтение работает без ручного переноса результата; права OAuth провайдера всё равно необходимы. Описание или тег удалённого инструмента не дают ему статус безопасного чтения.

Все другие вызовы идут через `call` в окне сервисов и требуют одноразового подтверждения **точного имени и аргументов**; подтверждение истекает через 180 секунд. Даже инструмент с названием GET/READ за пределами локального списка не выполняется автоматически. Meta tools `COMPOSIO_*`, удалённый workbench и proxy execution отключены. Instant usage отключён, но собственный тариф/квоты Composio сохраняются.

Для текстовых API-моделей существующий общий интерфейс позволяет прочитать данные и добавить результат в черновик; автоматического tool loop у них нет. Harness включать не требуется. Интеграция не подключает все аккаунты и не выдаёт им широкие права автоматически.

Удаление подключения удаляет локальную запись. Отзыв OAuth и удаление удалённой сессии/подключения выполняются в Composio/самом провайдере; интерфейс сообщает `remote_revocation_required`.

## Проверка и самостоятельная установка

Форк включает адаптер и мастер, **API key и аккаунты не входят в поставку**. До ввода ключа и авторизации приложений облачное подключение недоступно. Не создаётся фиктивное активное подключение. Автотесты используют локальный HTTP mock, не отправляют письма, посты или сообщения.

Проверки: `PYTHONPATH=custom/bridge custom/.venv/bin/python -m pytest custom/tests/test_composio.py custom/tests/test_connectors.py -q`.

Официальные источники:
- https://docs.composio.dev/docs/sessions-via-mcp
- https://docs.composio.dev/reference/api-reference/tool-router/postToolRouterSession
- https://docs.composio.dev/reference/api-reference/tool-router/getToolRouterSessionBySessionIdTools
- https://docs.composio.dev/reference/api-reference/tool-router/postToolRouterSessionBySessionIdExecute
- https://docs.composio.dev/reference/api-reference/tool-router/postToolRouterSessionBySessionIdLink
- https://docs.composio.dev/kb/guide/platform-session-tool-policies
