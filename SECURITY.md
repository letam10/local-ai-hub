# Chính sách bảo mật — Local AI Hub

Local AI Hub là ứng dụng Windows điều phối nhiều workflow AI cục bộ, vì vậy security boundary không chỉ nằm ở source code mà còn bao gồm API loopback, filesystem, model/runtime supply chain, worker output, project data và các ứng dụng external được Hub tham chiếu. Chính sách này mô tả những nguyên tắc bảo mật bắt buộc của repository và cách báo cáo sự cố.

## 1. Phạm vi áp dụng

Policy áp dụng cho source, scripts, desktop shell, WebView/UI, loopback API, Job Manager, Artifact Store, component/model/runtime managers, module adapter/worker, configuration schema, installer/package, update/repair flow và các thao tác filesystem do Local AI Hub quản lý.

V5/V6 được giữ như historical product lines. Security work mới ưu tiên dòng V7 đang phát triển; không nên suy luận rằng mọi historical branch đều nhận cùng mức hardening nếu chưa có xác nhận riêng.

## 2. Nguyên tắc bảo mật cốt lõi

- **Local-first:** API/MCP mặc định chỉ hoạt động local qua `127.0.0.1` hoặc stdio. Không thêm LAN/public bind, tunnel, port-forward hoặc remote-control surface nếu chưa được review rõ ràng.
- **Least authority:** UI/browser không được tự quyết raw filesystem path, executable, shell command, download URL, destination hoặc checksum override cho privileged operation.
- **Server-owned lifecycle:** component install/update/repair, job output publication và artifact registration phải dựa trên state/plan do server tạo và validate.
- **Fail closed:** khi ownership, source identity, catalog binding, path containment, checksum, license hoặc runtime evidence không đủ chắc chắn, operation phải từ chối hoặc chuyển sang trạng thái review thay vì giả thành thành công.
- **Preserve user data:** không xóa dữ liệu chỉ vì file nằm gần Temp/Output hoặc một job đã fail. Khi ownership không rõ, ưu tiên giữ dữ liệu.
- **Truthful state:** file/model tồn tại không đồng nghĩa `OPERATIONAL`; trạng thái production cần bằng chứng phù hợp.

## 3. Secret và dữ liệu nhạy cảm

Tuyệt đối không commit hoặc đưa vào log/PR/screenshot/config tracked các dữ liệu sau:

- API key, access token, refresh token, password, cookie, session credential.
- SSH/private key, signing key, service credential.
- Provider authentication material hoặc license token.
- Private voice reference, media cá nhân, dữ liệu project riêng tư.
- Machine-specific secret/config có thể xác định tài khoản hoặc hệ thống của người dùng.
- Model weights hoặc asset có điều khoản phân phối không cho phép đưa vào repository.

Repository private **không thay thế secret hygiene**. Nếu secret đã bị lộ: dừng push nếu còn có thể, revoke/rotate tại provider, loại secret khỏi source/history khi cần, kiểm tra nơi secret đã xuất hiện và báo cáo sự cố mà không paste lại giá trị secret.

## 4. API, WebView và network boundary

Canonical Hub API là loopback-only. Thay đổi network boundary phải được xem là security-sensitive.

Không được tự thêm:

- bind `0.0.0.0` hoặc LAN interface;
- public HTTP/WebSocket endpoint;
- tunnel/relay/port forwarding;
- browser-accessible shell execution;
- arbitrary subprocess launch;
- endpoint cho client chỉ định local path hoặc executable;
- credential logging/debug projection.

Nếu tương lai cần remote access, phải có threat model riêng gồm authentication, authorization, origin/session protection, transport security, rate limiting, auditability và explicit user opt-in trước khi bật.

## 5. Filesystem boundary và destructive action

`D:\LocalAIHub` là project filesystem boundary mặc định. Read-only inspection ngoài boundary có thể cần cho application/dependency discovery, nhưng destructive action ngoài boundary phải có explicit user approval cho đúng target.

Các nguyên tắc bắt buộc:

- Không follow symlink/junction/reparse point một cách mù quáng khi copy/move/delete/update.
- Path nằm lexically trong `D:\LocalAIHub` nhưng resolve ra ngoài qua reparse point được xem là **EXTERNAL** cho destructive operation.
- Không dùng `git clean -x`, `git clean -X`, `git clean -fdx` trên owner installation.
- Không tự xóa Models, Environments, runtime, Output, Config local state, Projects, Backups, user media, recovery bundle hoặc forensic evidence.
- Automatic cleanup chỉ áp dụng với Temp/Cache thực sự Hub-owned hoặc task-owned và ownership đã được xác minh.
- Administrator/full-access permission chỉ là quyền truy cập, không phải quyền phá hủy dữ liệu.

Trước một destructive external action cần báo rõ resolved target, lý do, quy mô dự kiến, trạng thái reparse nếu có và non-destructive alternative nếu tồn tại.

## 6. Model/runtime supply-chain

Model, runtime, archive và executable từ upstream là supply-chain input, không được mặc định coi là trusted chỉ vì URL tồn tại.

Production component lifecycle nên sử dụng:

- catalog-approved provider/source identity;
- pinned revision/release khi có thể;
- expected size và SHA-256/signature khi upstream cung cấp bằng chứng phù hợp;
- trusted fallback chỉ khi identity/revision/artifact tương đương đã được review;
- explicit handling cho authentication/license gate;
- bounded staging trước khi activation;
- archive traversal/reparse validation;
- receipt/provenance sau install/update.

Không dùng random mirror/fork làm fallback production. Không auto-update model/runtime/dependency chỉ vì upstream có phiên bản mới. `latest_upstream` và `latest_supported_by_Local_AI_Hub` phải được xem là hai giá trị khác nhau.

## 7. Component installation, update và repair

Public client chỉ nên gửi component ID, opaque plan ID hoặc confirmation token phù hợp. Server sở hữu catalog lookup, source selection, destination root và execution details.

Các operation nhạy cảm phải revalidate current catalog/context ngay trước mutation để tránh stale-plan/TOCTOU issue. Checksum mismatch, source drift, stale catalog binding, unsafe path, missing license/auth hoặc unsupported executor phải fail closed.

Repair không được xóa toàn bộ component nếu chỉ một leaf bị hỏng trừ khi contract và user confirmation yêu cầu reinstall. Update phải giữ khả năng rollback khi lifecycle hỗ trợ và không được làm mất working version trước khi candidate đủ verification.

## 8. Job output và Artifact Store

Output-producing worker là security-sensitive vì cleanup sai có thể xóa file không thuộc job.

Các invariant mục tiêu:

- Job-owned output phải có ownership/reservation hoặc transaction identity do server tạo trước/đúng lifecycle, không dựa đơn thuần vào path sau khi worker chạy.
- Arbitrary/pre-existing/foreign output không được claim chỉ vì nó nằm dưới `Output`.
- Cancel/failure cleanup chỉ được xóa file đã chứng minh thuộc exact job-owned scope.
- Ambiguous hoặc identity-changed file phải được preserve/manual-review thay vì destructive cleanup.
- Failed/cancelled job không được tự tạo published artifact như job thành công.
- Public artifact dùng opaque ID; raw local path không phải public authority.

Nếu một legacy producer chưa đạt ownership contract, nó phải bị giới hạn quyền cleanup/publication thay vì được coi là production-safe.

## 9. Config, backup, project và diagnostics

Tracked repository chỉ chứa config/schema mẫu; machine-local config không phải repository source.

Security-sensitive state cần:

- strict/bounded schema ở boundary phù hợp;
- atomic write khi cập nhật persistent metadata;
- containment và reparse checks cho restore/import;
- không expose raw local path, secret, unrestricted log hoặc exception internals qua diagnostics/public API;
- backup restore không được ghi đè hoặc escape sang target ngoài contract;
- project/workflow clear/delete chỉ tác động exact owned state.

## 10. External application và system software

AIRI và các application do installer khác quản lý được xem là external/system-managed. Hub không được tự di chuyển hoặc sửa installation của chúng nếu không có migration path được approve.

Không tự thay đổi:

- NVIDIA driver;
- CUDA/system runtime;
- system Python;
- global PATH;
- application installation ngoài Hub;
- OS security setting.

Các thay đổi này cần explicit user approval và task riêng.

## 11. Dependency và executable safety

Không thêm dependency/model version vào một feature không liên quan. Không vendor nguyên upstream repository nếu chưa được approve. Script/download helper phải ưu tiên deterministic/pinned input thay vì floating latest khi operation có khả năng mutate production state.

Không tạo shell-execution API hoặc arbitrary command bridge từ UI. Khi backend cần command-line tool, command/arguments phải do trusted server-side adapter xây dựng từ bounded inputs; không ghép trực tiếp untrusted client text thành shell command.

## 12. Logging, diagnostics và privacy

Log và diagnostics không được trở thành kênh rò rỉ dữ liệu. Tránh ghi:

- secret/token/auth header;
- full environment dump;
- raw user media/content khi không cần;
- private voice reference;
- raw external path trong public projection;
- command line chứa credential;
- unrestricted stderr/exception nếu có thể chứa secret/path.

Diagnostics public nên dùng finite status/error code và sanitized metadata. Bản forensic nội bộ nếu cần phải nằm trong boundary riêng, không tự publish lên Git/PR.

## 13. Báo cáo lỗ hổng

Không đăng công khai secret hoặc proof-of-concept có thể gây mất dữ liệu khi lỗ hổng chưa được xử lý. Với repository private, ưu tiên báo trực tiếp cho repository owner/maintainer; nếu GitHub Private Vulnerability Reporting hoặc Security Advisory được bật thì có thể dùng kênh đó.

Một báo cáo hữu ích nên có:

- phiên bản/branch/commit liên quan;
- component hoặc endpoint bị ảnh hưởng;
- điều kiện để tái hiện;
- impact thực tế hoặc worst credible impact;
- bước tái hiện tối thiểu;
- log/error code đã sanitize;
- đề xuất mitigation nếu có.

Không gửi credential, API key, private media hoặc dữ liệu người dùng thật trong báo cáo. Dùng fixture/redacted example khi có thể.

## 14. Phân loại ưu tiên

Có thể ưu tiên xử lý theo impact:

| Mức | Ví dụ |
|---|---|
| Critical | Remote/unauthorized code execution, secret extraction diện rộng, destructive filesystem escape ngoài boundary |
| High | Arbitrary file overwrite/delete, privilege boundary bypass, unauthorized artifact/config access, supply-chain verification bypass |
| Medium | Limited path disclosure, bounded authorization/state bypass, denial of service có tác động đáng kể |
| Low | Hardening gap nhỏ, information exposure hạn chế, issue cần local trusted access và impact thấp |

Severity cuối cùng cần xét cả exploitability, required access, data impact, scope và khả năng phục hồi; không chỉ dựa vào tên bug.

## 15. Những vấn đề thường không phải security vulnerability

Trừ khi có impact bảo mật cụ thể, các trường hợp sau thường là product/compatibility bug:

- model không tải được vì upstream offline;
- unsupported GPU/driver combination;
- model inference chậm;
- feature hiển thị `MANUAL_IMPORT_ONLY` hoặc `UNAVAILABLE` đúng contract;
- provider yêu cầu login/license;
- output quality thấp;
- backend chưa được smoke-tested và bị giữ ở `PARTIAL`/`INSTALLED_UNVERIFIED`.

Ngược lại, việc bypass các state đó để thực thi/download/delete trái contract có thể là security issue.

## 16. Development security gates

Trước push của thay đổi nhạy cảm nên chạy các gate phù hợp:

- secret scan/tracked-large-file check;
- targeted security/contract tests;
- Python/JavaScript syntax validation cho file đã sửa;
- `git diff --check`;
- repository validation/CI;
- bounded smoke khi operation thực sự cần runtime evidence.

Không benchmark nếu task không yêu cầu. Không dùng test fixture để tuyên bố production security hoặc operational status rộng hơn bằng chứng thực tế.

## 17. Khi phát hiện sự cố

Nếu nghi có security incident:

1. Dừng action có thể làm tình hình xấu hơn.
2. Giữ evidence cần thiết; không cleanup vội log/state quan trọng.
3. Thu hồi/rotate credential nếu secret bị lộ.
4. Cô lập source/component/path có vấn đề nếu làm được mà không phá dữ liệu.
5. Ghi lại commit/version, timeline và sanitized evidence.
6. Sửa root cause và thêm regression test trước khi mở lại capability.
7. Nếu artifact/release đã bị ảnh hưởng, tạo release mới; không silently move/rewrite immutable tag chỉ để che sự cố.

---

Mục tiêu của Local AI Hub là giữ security boundary **local-first, server-owned, path-safe, supply-chain-aware và non-destructive by default**. Khi thiếu bằng chứng về quyền sở hữu, nguồn, checksum, state hoặc phạm vi tác động, hệ thống phải ưu tiên từ chối hoặc bảo toàn dữ liệu thay vì tự suy đoán và tiếp tục mutation.
