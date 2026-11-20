# Black Duck SCA SSO Migration

## 지원 범위

세 Python CLI와 공유 API 클라이언트입니다. 배포된 서버의 공개 REST API 명세가 필요합니다. config.example.json의 null은 명세 확인이 필요한 항목입니다. 스크립트는 설정 없이 해당 작업에서 중단됩니다. 내부 서버의 URL, 토큰 및 계정 데이터는 내부 PC에서만 입력합니다.

REST API Developers Guide는 설치 서버의 Help 메뉴 또는 /api-doc/public.html에서 확인합니다. API 토큰 교환 경로는 /api/tokens/authenticate입니다.

- [REST API 접근 및 media type](https://docs.blackduck.com/r/blackduck/2025.1/black-duck-documentation/accessing-the-rest-apis.html)
- [API 인증 예시](https://docs.blackduck.com/r/blackduck/2026.7/black-duck-documentation/authenticating-with-the-api.html)

## 입력

승인된 방식으로 준비한 쉼표 구분 CSV를 사용합니다. 기본 열 이름은 `Tool`, `Domain`, `Source Account`, `Target Account`, `Account Type`입니다. 사용하는 양식에 맞춰 `config.json`의 `application_columns`에 같은 순서로 실제 열 이름을 지정합니다. 다른 열이 있어도 됩니다.

`Tool`이 Black Duck이고 Domain이 `--domain`과 일치하는 행을 처리합니다. ID는 대소문자와 접두사를 유지합니다. ID 한 개당 필드 하나를 사용하고, 여러 대상은 여러 행으로 입력합니다. 계정 유형은 `allowed_id_types`로 검증합니다. 신규 대상은 External 계정으로 생성하고, 기존 대상이 Internal이거나 비활성이면 계획 생성이 중단됩니다.

기본 인코딩은 UTF-8(BOM 허용)입니다. 신청 CSV 또는 승인 CSV가 CP949로 저장된 경우 해당 명령에 `--encoding cp949`를 추가합니다. 출력은 UTF-8 BOM입니다. 앞에 0이 있는 ID는 CSV 원문에도 0이 남아 있어야 합니다.

## 내부 서버에서 API 설정

config.example.json을 config.json으로 복사합니다. domains에는 CSV Domain 값과 대응하는 HTTPS 서버를 입력합니다. 서로 다른 인스턴스의 API 형식이 다르면 설정 파일을 나눕니다. 사내 CA가 필요하면 ca_bundle에 PEM 파일 경로를 설정합니다. allowed_id_types에는 신청서에서 자동화 대상으로 승인한 실제 값을 넣습니다.

설치 서버 문서에서 각 operation의 method, path, Accept, Content-Type과 요청 본문을 옮깁니다. 조회 operation의 items는 JMESPath 표현식입니다. 다음 형식으로 정규화합니다.

| Operation | 반드시 반환할 정규화 필드 |
|---|---|
| users | username, id, type (INTERNAL 또는 EXTERNAL), active (boolean) |
| user_group | resource (그룹 ID), role (멤버십이면 빈 문자열) |
| project | resource (프로젝트 ID), role (역할 ID) |
| project_group | resource (프로젝트 그룹 ID), role (역할 ID) |
| global_role | resource (빈 문자열), role (역할 ID) |
| project_owner | resource (직접 소유 프로젝트 ID), role (빈 문자열 가능) |

프로젝트, 프로젝트 그룹, Global Role 조회 식에는 사용자에게 직접 부여된 항목을 사용합니다. User Group을 통해 상속되는 권한이 섞이면 실명 계정에 직접 부여될 수 있습니다. role 권한이 배열로 반환되면 JMESPath에서 권한마다 한 항목이 되도록 펼칩니다. 프로젝트 소유권 조회는 비활성화 단계에서도 확인합니다.

아래는 **형식 설명용 가상 API**입니다. 실제 BDH 경로 및 필드를 설치 서버 문서에 맞춰 대체합니다.

```json
{
  "method": "GET",
  "path": "/api/EXAMPLE",
  "query": {"search": "{username}"},
  "Accept": "REPLACE_FROM_API_GUIDE",
  "items": "items[].{username: login, id: id, type: accountType, active: enabled}",
  "pagination": {"offset": "offset", "limit": "limit", "total": "totalCount", "size": 100}
}
```

목록이 한 페이지로 완전하게 반환되는 경우 pagination을 생략합니다. offset 방식과 다른 API는 클라이언트 구현을 해당 방식에 맞춰야 합니다. users 조회는 생성 직전과 비활성화 검증에도 사용하므로 문서상 옵션으로 비활성 계정을 포함해야 합니다.

변경 operation은 method, path, 선택적인 query, Content-Type 및 body를 지정합니다. 본문은 JSON이며 문자열 값에 변수를 넣을 수 있습니다. 사용자 조회와 생성에서 {username}, 생성에서 {id_type}, 멤버십 및 Role 작업에서 {user_id}, {username}, {resource}, {role}, 비활성화에서 {user_id}, {username}을 사용할 수 있습니다. Role 부여에는 기존 역할을 보존하는 추가 API를 사용합니다. 전체 목록을 덮어쓰거나 여러 계정을 교체하는 API를 사용해야 할 경우 해당 기능은 별도 구현이 필요합니다. External 생성 API에서 이름이나 이메일이 필수라면 승인된 입력 열을 추가해 매핑해야 합니다.

## 실행 예시 (PowerShell)

```powershell
py -m pip install -r requirements.txt
# 환경 변수 BDH_TOKEN에 조회용 토큰을 설정한 뒤 실행
py ExportMigrationPlan.py --config config.json --applications applications.csv --domain DOMAIN_FROM_CSV --out plan-01
```

plan-01의 snapshot.json은 이관 전 계정과 직접 부여된 권한입니다. plan.json은 적용 대상 목록입니다. 둘을 보존하고 검토할 CSV 사본에서 approved 열만 YES / NO / 공란으로 작성합니다. 승인 CSV는 UTF-8로 저장하고 ID와 원본 행 번호가 자동 변환되지 않도록 합니다. Global Role과 접근 범위가 넓은 User Group은 권한 확인 후 승인합니다. 소유권은 MANUAL_OWNER로 출력되며 프로젝트별 후임자를 별도 지정합니다.

```powershell
py ApplyMigrationPlan.py --config config.json --plan-dir plan-01 --review approved.csv --phase provision --result preview.csv
# 쓰기 토큰을 같은 환경 변수에 설정하고 결과 경로는 매번 새 이름을 지정
py ApplyMigrationPlan.py --config config.json --plan-dir plan-01 --review approved.csv --phase provision --result applied.csv --execute
py ApplyMigrationPlan.py --config config.json --plan-dir plan-01 --review approved.csv --phase deactivate --result deactivated.csv --execute
```

--execute 생략 시 실제 조회와 예정 변경 검증만 수행합니다. 아직 생성하지 않은 External의 권한은 WAITING_FOR_EXTERNAL로 표시됩니다. 쓰기 후 재조회한 결과가 일치해야 VERIFIED가 기록됩니다. 요청 결과가 불명확하거나 일부 실패한 경우 같은 승인 CSV와 새 결과 파일로 재실행하면 이미 반영된 항목은 ALREADY_DONE으로 표시됩니다. 동시 변경을 피하기 위해 같은 Domain에서 적용 작업은 한 번에 하나씩 실행합니다.

가용 좌석이 충분하면 실명 계정 및 권한 적용을 검증한 뒤 비실명 계정을 비활성화합니다. 좌석을 먼저 확보해야 한다면 비활성화 승인 전에 원본 스냅샷과 영향 범위를 검토하고, 비활성화 후 좌석 반환을 확인합니다. 스크립트는 좌석 수와 사람의 SSO 로그인 성공 여부를 직접 검증하지 않습니다. 개발계에서 실제 SSO 로그인, 좌석 반영, 각 권한의 조회 및 부여를 확인한 뒤 운영에 적용합니다.


## 도메인별 계정과 권한 조회

사용자 목록 기본값의 근거는 [Black Duck Python SDK 사용자 API](https://github.com/blackducksoftware/hub-rest-api-python/blob/master/blackduck/Users.py)와 [공식 전체 사용자 조회 예제](https://github.com/blackducksoftware/hub-rest-api-python/blob/master/examples/client/get_users.py)입니다. 설치 버전별 지원 범위는 [Black Duck REST API 안내](https://docs.blackduck.com/r/integration_explorer/latest/integrations-finder/apis-for-black-duck-products.html)를 참고합니다.

신청 CSV와 관계없이 구성된 서버에서 계정을 조회합니다. `list_users`에는 사용자명 검색 조건이 없는 전체 조회 API를 설정합니다. `config.example.json`은 공식 Python SDK의 사용자 조회를 바탕으로 `/api/users`, `user-4+json`, `limit`·`offset`을 기본으로 제공합니다. `users`와 동일한 정규화 필드가 필요하며, 활성·비활성 계정이 모두 반환되도록 조회 조건과 페이지네이션을 설정합니다. 인스턴스가 서로 다른 API 형식을 쓰면 구성 파일을 나눠 실행합니다.

```powershell
py ExportAccountInventory.py --config config.json --domain example --out inventory-example
py ExportAccountInventory.py --config config.json --all-domains --out inventory-all
py ExportAccountInventory.py --config config.json --all-domains --out accounts-all --accounts-only
```

`--accounts-only`는 사용자 목록 설정만 사용합니다. `users.csv`와 `summary.json`을 생성하며 권한 행 수는 조회하지 않았음을 나타내는 `null`입니다. 기존 설정 파일을 사용 중인 경우 새 예시의 `operations.list_users` 항목을 복사합니다. 서버에서 반환되는 활성·비활성 계정 수는 관리자 화면과 대조합니다.

- `users.csv`는 도메인, 계정 ID, 사용자명, Internal/External 유형, 활성 상태를 기록합니다.
- `permissions.csv`는 User Group 가입, 직접 Global Role, Project Role, Project Group Role, Project Owner를 기록합니다. API 응답에서 `resource_name`과 `role_name`을 정규화하면 이름도 출력합니다.
- `summary.json`은 도메인별 활성·비활성 계정 수와 권한 행 수를 기록합니다.

User Group을 통해 부여되는 권한도 추출하려면 `group_global_role`, `group_project`, `group_project_group`을 그룹 ID(`{group_id}`) 기반의 읽기 API로 설정하고 `--include-inherited`를 추가합니다. 각각 기존 Role 조회와 같은 형태(`resource`, `role`)로 반환합니다. 보고서의 `assignment` 및 `via_group`에서 부여 경로를 확인할 수 있습니다. 보고서는 조회된 역할과 멤버십을 나열합니다. 전역 역할이나 프로젝트 그룹이 영향을 미치는 개별 프로젝트까지 유효 접근 권한으로 전개하지는 않습니다.

인증 토큰을 교환한 뒤 계정·권한 데이터는 GET으로 조회합니다. API 호출이 실패하면 보고서 생성을 중단합니다. 응답 성공은 토큰에 전체 조회 범위가 있음을 보장하지 않으므로 첫 실행 시 도메인별 계정 수와 샘플 권한을 관리자 화면과 대조합니다.
