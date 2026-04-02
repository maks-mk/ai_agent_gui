# Autonomous AI Agent (GUI)

Версия: `v0.63.2b`

Desktop AI-агент с графовым runtime (`LangGraph`) и GUI на `PySide6`.
Приложение ориентировано на повседневную разработку: работа с файлами проекта, запуск инструментов, безопасные approvals для рискованных действий и удобная история чатов по проектам.

## Ключевые возможности

- Чат-интерфейс с persistent-сессиями по проектам.
- Sidebar с историей чатов, быстрым переключением и удалением сессий.
- Transcript с компактным отображением шагов агента и инструментов.
- Composer с автоподстройкой высоты и отправкой `Enter` (`Shift+Enter` — новая строка).
- Быстрое добавление файлов через `@`:
  - popup-список рядом с курсором,
  - фильтрация по имени/пути,
  - навигация `↑/↓`, выбор `Enter`, закрытие `Esc`,
  - приоритет файлов из корня проекта, затем из подпапок.
- История запросов в composer:
  - навигация `↑/↓` как в терминале,
  - работает по текущей сессии,
  - восстановление после перезапуска из transcript.
- Approval-поток для mutating/destructive инструментов (`Approve`, `Deny`, `Always for this session`).
- Поддержка MCP-инструментов (включая Context7 при соответствующей конфигурации).
- Runtime-статусы (`Thinking`, `Self-correcting`, `Ready`, ошибки) в UI.
- Профили моделей в GUI:
  - окно `Settings` (CRUD профилей),
  - селектор активной модели рядом с `+` в composer,
  - безопасное переключение только вне `busy/approval`,
  - применение выбранной модели к **следующему** запросу без сброса чата.
- Автоподхват модели из `.env`:
  - при первом запуске env-модель записывается в настройки,
  - при последующих запусках env-модели аккуратно подмешиваются в профильный список,
  - одинаковые профили не дублируются.

## Архитектура

- [`agent.py`](agent.py): сборка агентного графа (`summarize -> agent -> tools/approval -> stability_guard`), LLM, реестр инструментов, checkpoint backend.
- [`main.py`](main.py): главное окно GUI, меню, sidebar, transcript, composer, обработка UI-событий.
- [`core/gui_runtime.py`](core/gui_runtime.py): runtime-контроллер, запуск графа, стриминг событий, переключение/восстановление сессий.
- [`core/gui_widgets.py`](core/gui_widgets.py): UI-виджеты (composer, transcript, popup, tool cards, sidebar).
- [`core/nodes.py`](core/nodes.py): узлы графа, self-correction (`stability_guard`), loop-guards, tool-preflight и safety flow.
- [`core/session_store.py`](core/session_store.py): хранение и индекс сессий.
- [`core/ui_theme.py`](core/ui_theme.py): единая тема и стили.
- [`core/config.py`](core/config.py): загрузка env-конфигурации.
- [`tools/tool_registry.py`](tools/tool_registry.py): локальные + MCP инструменты, метаданные, политика безопасности.

## Поведение self-correction

- В графе нет отдельного LLM-узла критика: контроль стабильности выполняет детерминированный `stability_guard`.
- При незакрытой ошибке инструмента выполняется максимум **1** внутренний auto-retry в рамках user turn.
- Если после auto-retry проблема не закрыта или требуется пользовательский ввод, агент делает безопасный handoff без ложного сообщения об успехе.

## Хранение данных

- Checkpoint state: `CHECKPOINT_SQLITE_PATH` (или другой backend).
- Активная сессия: `SESSION_STATE_PATH` (по умолчанию `.agent_state/session.json`).
- Индекс чатов: `.agent_state/session_index.json`.
- Логи запусков: `RUN_LOG_DIR` (JSONL).
- Профили моделей: `.agent_state/config.json`:
  - `active_profile: str | null`
  - `profiles: [{id, provider, model, api_key, base_url}]`

При старте runtime текущая активная сессия и transcript автоматически восстанавливаются.

## Профили моделей и ENV

- Источник правды для профилей в GUI — `.agent_state/config.json`.
- Если `config.json` отсутствует или пустой по профилям, профиль создается из `.env`.
- Если `config.json` уже существует, env-профили добавляются без дублирования существующих.
- `active_profile` сохраняется при merge (если валиден), иначе выбирается первый доступный профиль.
- Для `provider=gemini` поле `base_url` в UI отключено и не используется.
- Для `provider=openai` `base_url` доступен и сохраняется.

## Быстрый старт

1. Создать виртуальное окружение:

```bash
python -m venv venv
```

2. Установить зависимости:

```bash
pip install -r requirements.txt
```

3. Подготовить `.env`:

```powershell
Copy-Item env_example.txt .env
```

4. Запустить приложение:

```bash
python main.py
```

## Основные переменные окружения

### LLM-провайдер

- `PROVIDER=gemini|openai`
- `GEMINI_API_KEY`, `GEMINI_MODEL`
- `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL`
- (опционально, generic bootstrap) `MODEL`, `API_KEY`, `BASE_URL`

### Runtime и persistence

- `PROMPT_PATH`
- `MCP_CONFIG_PATH`
- `CHECKPOINT_BACKEND=sqlite|memory|postgres`
- `CHECKPOINT_SQLITE_PATH`
- `CHECKPOINT_POSTGRES_URL`
- `SESSION_STATE_PATH`
- `RUN_LOG_DIR`

### Инструменты и безопасность

- `MODEL_SUPPORTS_TOOLS`
- `ENABLE_FILESYSTEM_TOOLS`
- `ENABLE_SEARCH_TOOLS` (+ `TAVILY_API_KEY`)
- `ENABLE_SYSTEM_TOOLS`
- `ENABLE_PROCESS_TOOLS`
- `ENABLE_SHELL_TOOL`
- `ENABLE_APPROVALS`
- `ALLOW_EXTERNAL_PROCESS_CONTROL`

### Лимиты

- `MAX_TOOL_OUTPUT`
- `MAX_SEARCH_CHARS`
- `MAX_FILE_SIZE`
- `MAX_READ_LINES`
- `MAX_BACKGROUND_PROCESSES`

### Контекст и retry

- `SESSION_SIZE`
- `SUMMARY_KEEP_LAST`
- `MAX_RETRIES`
- `RETRY_DELAY`

### Диагностика

- `DEBUG`
- `STRICT_MODE`
- `LOG_LEVEL`
- `LOG_FILE`

## Управление сессиями

- `New Session` создает новый чат в текущем проекте.
- `Open Folder` переключает рабочую папку и контекст сессий.
- Sidebar показывает историю чатов с группировкой по проектам.

## Горячие клавиши в Composer

- `Enter` — отправить запрос.
- `Shift+Enter` — новая строка.
- `@` — открыть popup файлов.
- `↑/↓` — навигация по popup или истории запросов (в зависимости от контекста).
- `Esc` — закрыть popup.

## Тесты

Запуск полного набора:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Ключевые наборы:

- [`tests/test_cli_ux.py`](tests/test_cli_ux.py)
- [`tests/test_critic_graph.py`](tests/test_critic_graph.py)
- [`tests/test_model_profiles.py`](tests/test_model_profiles.py)
- [`tests/test_runtime_refactor.py`](tests/test_runtime_refactor.py)
- [`tests/test_stream_and_filesystem.py`](tests/test_stream_and_filesystem.py)
- [`tests/test_tooling_refactor.py`](tests/test_tooling_refactor.py)

## Безопасность

- Не храните реальные API-ключи в репозитории.
- Включайте `ENABLE_SHELL_TOOL=true` только при необходимости и с approvals.
- Для production persistence рекомендуется `postgres` backend.
- Проверяйте политику инструментов перед запуском mutating/destructive действий.
