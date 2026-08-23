# Local AI Hub V8 Wave 0 — Storage & Transaction Authority

Wave 0 xây nền tảng an toàn mới cho dữ liệu mutable trước khi V8 mở rộng Component Lifecycle, AI runtime hoặc UI. Đây là lớp bổ sung trên V7 đã freeze; nó không tự thay thế các callsite V7 cũ cho tới khi có package migration và Windows acceptance riêng.

## Mục tiêu

Luồng authority chuẩn của V8:

```text
Fixed Storage Root Authority
→ Pre-execution Reservation
→ Private Transaction
→ Copy-once Managed Object
→ Durable Authorization
→ ONE FINAL PUBLIC METADATA COMMIT
```

Invariant bắt buộc: **NO VALID RESERVATION = NO V8 PUBLICATION**.

## Storage Authority

`src/platform/storage_authority.py` là authority duy nhất cho mutable root mới của V8. Root được suy ra từ `HubPaths`, không nhận raw root từ browser/caller. Trước khi tạo hoặc ghi file, authority kiểm tra lexical containment, toàn bộ ancestor đang tồn tại, symlink/junction/reparse và identity của root. `RootLease` là short-lived proof; nếu root đổi identity thì thao tác fail closed.

Các thao tác cleanup chỉ xóa regular file khi exact identity còn khớp. Foreign/pre-existing/replaced file được giữ lại thay vì suy đoán ownership.

## Transaction Store

`src/services/transaction_store.py` dùng SQLite chuẩn của Python làm authoritative control-plane journal. Database nằm trong server-owned Config root và chỉ lưu opaque ID, relative object key, hash/size/identity và finite state. Không lưu raw public workstation path.

Quan hệ bắt buộc được enforce bằng khóa/foreign key:

```text
Job
└─ Reservation
   └─ Transaction
      └─ Artifact
         └─ Managed Object
```

`transaction_id`, `reservation_id` và `job_id` phải khớp cùng một tuple. Một transaction hợp lệ không thể được publish bằng reservation/job khác.

Publication dùng hai bước nội bộ: `preparing → authorized → committed`. Artifact ở `staged` không xuất hiện trong public query. Final commit đổi artifact sang `published`, transaction sang `committed` và reservation sang `published` trong cùng một SQLite transaction. Storage hold revalidate identity khi boundary đóng; nếu POSIX rename/replace được phát hiện sau SQLite commit, một compensating transaction xoá public index rows và chuyển journal về `aborted` trước khi caller nhận success. Cleanup vẫn identity-attested và không xoá foreign bytes. Không có mandatory manifest write sau public commit.

## Immutable managed object

`src/services/output_authority.py` coi producer path chỉ là input material. Output được copy bằng exclusive creation vào `.hub-v8/objects/<shard>/<opaque-object-id>` dưới fixed Output root. Source được kiểm tra identity trước/sau copy; managed object có size/hash/file identity riêng. Public metadata chỉ tham chiếu opaque artifact ID; resolve kiểm tra object identity và fail closed nếu object bị thay thế.

Việc producer/staging path bị đổi sau publication không làm đổi bytes của public artifact. Cleanup/reconciliation chỉ tác động exact transaction-owned managed object đã attested.

## Các lỗi P0 V7 được xử lý ở lớp nền

Wave 0 được thiết kế trực tiếp từ failure evidence của V7:

1. Output root là symlink/junction/reparse trỏ ra ngoài → Storage Authority từ chối trước reservation/write.
2. Transaction được publish với reservation ID khác → DB foreign-key/binding query từ chối.
3. Public artifact trỏ vào mutable producer/staging path → V8 publish managed copy-once object riêng.
4. Artifact trở thành public trước khi proof hoàn tất → staged luôn private, final DB commit là visibility mutation cuối.
5. Persistence fail sau public commit → không còn mandatory reservation/manifest write sau final commit.

## Phạm vi chưa kích hoạt

Wave 0 chỉ cung cấp foundation service và synthetic contracts. Các V7 callsite hiện dùng `artifact_store.py`, `HubJobManager`, `DurableWorkEngine` chưa được tự động chuyển sang V8 authority trong commit này. Lý do: migration đó cần Windows reparse/handle-race acceptance và regression đầy đủ trên installation thật trước khi thay production path.

Không có model/runtime download, AI inference, GPU smoke, FFmpeg execution, server/browser launch, provider request, CUDA/driver change hoặc release/tag mutation trong Wave 0.

## Gate chuyển sang package migration

Package production-callsite migration sau này chỉ được phép bật V8 Output Authority khi có: Windows junction/reparse fixtures, concurrent same-name jobs, crash/restart reconciliation, multi-artifact partial failure, immutable-object drift, exact reservation/job mismatch, foreign/pre-existing preservation và full regression trên installation thật.
