실적 신고가 스크리너

매출 또는 영업이익이 분기 기준과 최근 4분기 합계 기준으로 모두 사상 최고인 종목 가운데, 버핏식 품질·안전마진 조건과 차트 보조 조건을 통과한 종목을 보여주는 웹앱입니다.

구성
docs/index.html  웹앱 (GitHub Pages로 공개)
docs/data.json   스크리닝 결과 (처음에는 가상의 샘플 데이터)
scripts/fetch.py DART 재무와 시세를 받아 data/cache에 저장
scripts/screen.py 지표를 계산해 docs/data.json 생성
.github/workflows/daily.yml 평일 17:47에 위 두 스크립트를 자동 실행

처음 설정
1. opendart.fss.or.kr에서 인증키를 발급받습니다.
2. GitHub에 새 저장소를 만들고 이 폴더의 파일을 모두 올립니다.
3. 저장소 Settings > Secrets and variables > Actions > New repository secret에서 이름 DART_API_KEY, 값에 인증키를 넣습니다.
4. Settings > Pages에서 Source를 Deploy from a branch, 브랜치 main, 폴더 /docs로 지정합니다.
5. Actions 탭 > daily-screen > Run workflow로 첫 실행을 합니다. 첫 실행은 과거 재무를 모두 받느라 30분 이상 걸릴 수 있고, 이후에는 최근 2개 연도만 다시 받습니다.

계산 기준과 한계
재무는 DART 다중회사 주요계정(연결 우선, 없으면 별도)을 쓰며 2015년부터 제공됩니다. 그래서 사상 최고는 2015년 이후 기준입니다.
과거 PER은 분기 말 종가에 현재 상장주식수를 곱해 계산하므로 증자나 자사주 소각이 있었던 종목은 오차가 생깁니다.
결산월이 12월이 아닌 종목은 분기 라벨이 달력 분기와 어긋날 수 있습니다.
그레이엄 내재가치 = √(22.5 × EPS × BPS), EPS는 최근 4분기 순이익 기준입니다.
금융업 판정은 DART 업종코드 64·65·66입니다.
