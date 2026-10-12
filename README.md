# Research Monitor

토목·구조 엔지니어링 뉴스 모니터링 시스템.

6개 언론사 기사 수집 → 품질검사 → Rubric v2 분류 → 동일 사건 중복 제거 → TXT·HTML 보고서 생성.

## 실행

```bash
pip install -r requirements.txt
python -m playwright install chromium
streamlit run app.py
```

실행 환경에 `OPENAI_API_KEY`와 `RESEARCH_MONITOR_PASSWORD`를 설정해야 합니다.
API 키와 실행 결과는 Git에 저장하지 않습니다.
