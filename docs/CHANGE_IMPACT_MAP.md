# V7 operational change impact map

| Concern | Owner | Persistent state |
| --- | --- | --- |
| Catalog/source identity | `src/services/productization/` | tracked catalog examples/schema |
| Source availability | `src/services/operational_closure/source_availability.py` | ignored source cache |
| Install/import/bundle | `src/services/component_installer/` | ignored receipts/history |
| Runtime/model evidence | `src/services/operational_closure/evidence.py` | ignored path-free evidence |
| Update checking/apply | `src/services/operational_closure/` | ignored settings/receipts |
| Component UI | `src/ui/features/components/` | no filesystem access |
| Update Center UI | `src/ui/features/updates/` | no network or source paths |
| Desktop/native picker | `src/app/main.py` nested bridge | short-lived server selection |

All flows use the same API → manager → adapter → Job/Artifact architecture for
owner and fresh installations. No Web/Cloud control plane is introduced.
