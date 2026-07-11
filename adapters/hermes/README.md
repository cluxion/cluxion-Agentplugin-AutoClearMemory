# Hermes adapter

Hermes는 pip entry point로 ForgetForge를 로드합니다.

`~/.hermes/config.yaml` 에 추가한 뒤 Hermes를 재시작하고, 어댑터 자산을 설치하세요.

```yaml
plugins:
  enabled:
    - cluxion-agentplugin-autoclearmemory
```

```bash
forgetforge init --agents=hermes
```

## 연결된 AI 도구

| Tool | 용도 |
|------|------|
| `forgetforge_store` | 저장/갱신 (contradiction warnings) |
| `forgetforge_recall` | FTS 검색 + retrieval 기록 (`layer` 선택, 기본 `explicit`) |
| `forgetforge_status` | tier·건강 상태 |
| `forgetforge_keep` | `#keep_forever` |
| `forgetforge_forget` | `#forget_this` |
| `forgetforge_unforget` | forget 취소 (복원) |
| `forgetforge_doctor` | 어댑터/DB 진단 |
| `forgetforge_import_brief` | preprocessing/supercoder brief 수입 |
| `forgetforge_hot_context` | hot tier 블록 (또는 `pre_llm_call` hook) |

연결된 AI는 recall 결과를 읽고 응답 맥락에 반영합니다. Hermes는 hot tier를 `pre_llm_call` hook으로 자동 inject합니다.

## 슬래시 (0.3.14+)

| Slash | 도구 대응 |
|---|---|
| `/forgetforge-recall <query>` | `forgetforge_recall` |
| `/forgetforge-status` | `forgetforge_status` |
| `/forgetforge-doctor` | `forgetforge_doctor` |
