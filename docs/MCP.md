# MCP (Model Context Protocol)

`mcp.json` хранит конфигурацию опциональных MCP-серверов и сохранённое состояние встроенных инструментов. В текущем шаблоне доступны `context7` (remote HTTP, read-only, включён) и `sequential-thinking` (stdio, выключен). Чтобы изменить состояние сервера, выставьте для него нужное значение `enabled` и перезапустите приложение.

Служебный объект `_builtin_tools` не является MCP-сервером. UI записывает в него пользовательские overrides для встроенных инструментов, например `"download_file": false`. Если override отсутствует, состояние определяется соответствующим фиче-флагом из `.env`.

## Policy

| `policy.read_only` | Поведение |
|---|---|
| `true` | Tool считается read-only, approval не требуется |
| `false` | Требует approval |
| не указан | Консервативный режим: approval по умолчанию |

## Пример подключения удалённого сервера

```json
{
  "context7": {
    "type": "remote",
    "url": "https://mcp.context7.com/mcp",
    "transport": "http",
    "enabled": true,
    "policy": {
      "read_only": true
    }
  }
}
```
