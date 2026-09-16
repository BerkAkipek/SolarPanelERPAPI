# General design principles

These principles apply to all work in this repository.

| # | Principle | Rule |
| --- | --- | --- |
| **1** | **Keep it simple** | Add complexity only when a real requirement justifies it. |
| **2** | **One source of truth** | Every business fact and rule has one authoritative owner. |
| **3** | **Protect invariants** | Prevent invalid states with validation, domain rules, and DB constraints. |
| **4** | **Explicit workflows** | Important state changes happen through clear business operations, not arbitrary updates. |
| **5** | **Clear boundaries** | Modules have focused responsibilities, high cohesion, low coupling, and no circular dependencies. |
| **6** | **Design for failure** | Use transactions, idempotency, and concurrency protection where correctness matters. |
| **7** | **Preserve important history** | Keep audit-worthy events and state changes instead of silently overwriting them. |
| **8** | **Make failures understandable** | Errors and logs must explain what happened, why, and with which relevant inputs. |
| **9** | **Test behavior** | Test observable business behavior rather than internal implementation details. |
| **10** | **Test failure paths** | Always test invalid input, conflicts, permissions, missing data, retries, and boundary cases. |

## Solar Panel ERP API

This project provides an API for an ERP system for the solar panel industry.
Apply the following domain principles and technology choices alongside the general principles above.

## ERP design principles

| # | Principle | What it means |
| --- | --- | --- |
| **1** | **End-to-end traceability** | Trace raw material/component → batch → panel → shipment → installation → maintenance → replacement/recycling. |
| **2** | **Single source of truth** | Customer, supplier, panel model, component, warehouse, project, employee, etc. should have one authoritative representation. |
| **3** | **Separate product from physical unit** | `PanelModel` describes *what something is*. `PanelUnit` describes *this specific manufactured panel*. |
| **4** | **Lot + serial genealogy** | A defective EVA batch, cell lot, junction box, glass supplier, etc. should be traceable to every affected panel. |
| **5** | **Quality is part of the domain** | Inspections, measurements, test results, NCRs, certifications and corrective actions should not be miscellaneous attachments. |
| **6** | **Projects and manufacturing are separate flows** | Producing 5,000 modules and installing a 2 MW plant are different business processes even when they share inventory and accounting. |
| **7** | **Inventory has state, not just quantity** | 100 panels can mean 60 available, 20 reserved, 10 quarantined, 5 damaged and 5 in transit. |
| **8** | **Financial events follow physical events** | Purchasing, consumption, production, shipment, installation and service should naturally create the relevant cost/revenue events. |
| **9** | **History beats overwriting** | Prices, BOMs, suppliers, panel locations, ownership, warranty state and inspections should preserve history. |
| **10** | **Critical transactions are auditable** | Who changed something, when, from what, to what, and why should be recoverable. |
| **11** | **Workflow is explicit** | Use controlled states rather than arbitrary flags. Example: `planned` → `approved` → `released` → `in_production` → `completed`. |
| **12** | **Modules share a common core** | Procurement, manufacturing, warehouse, sales, projects, quality, service and finance should integrate through common domain objects instead of duplicating them. |

## Technology stack

Preserve the priorities below. Redis and Kubernetes are optional technologies to introduce when their stated needs arise.

| Katman | Teknoloji | Kullanım Amacı | Öncelik |
| --- | --- | --- | ---: |
| **Backend** | Python | Ana uygulama dili | 🔴 |
| **API Framework** | FastAPI | REST API, validation, OpenAPI, dependency injection | 🔴 |
| **Database** | PostgreSQL | ERP’nin ana ilişkisel veri deposu | 🔴 |
| **ORM** | SQLAlchemy 2.x | Veri erişimi ve transaction yönetimi | 🔴 |
| **Migration** | Alembic | Şema versiyonlama ve migration | 🔴 |
| **Validation** | Pydantic | Request/response modelleri ve veri doğrulama | 🔴 |
| **Frontend** | React | Basit ERP kullanıcı arayüzü | 🟠 |
| **Frontend Language** | TypeScript | Daha güvenli ve sürdürülebilir frontend geliştirme | 🟠 |
| **Server State** | TanStack Query | API verisi, cache ve request yönetimi | 🟠 |
| **API Style** | REST | Backend/frontend ve üçüncü parti entegrasyonları | 🔴 |
| **Authentication** | JWT + OAuth2 flow | Kullanıcı kimlik doğrulama | 🟠 |
| **Authorization** | RBAC | ERP rol ve yetki yönetimi | 🟠 |
| **Testing** | pytest | Unit ve integration testleri | 🔴 |
| **API Testing** | FastAPI TestClient / httpx | Endpoint ve akış testleri | 🟠 |
| **Containerization** | Docker | Uygulamanın paketlenmesi | 🟠 |
| **Local Orchestration** | Docker Compose | FastAPI + PostgreSQL + diğer servisler | 🟠 |
| **Version Control** | Git | Kaynak kod ve branch yönetimi | 🔴 |
| **CI/CD** | GitHub Actions | Test, build ve deployment pipeline | 🟠 |
| **Documentation** | OpenAPI / Swagger | API dokümantasyonu ve demo | 🔴 |
| **Optional Cache/Queue** | Redis | Cache, background jobs veya queue ihtiyaçları | 🟡 |
| **Optional Deployment** | Kubernetes | İleri aşamada orchestration ve scaling | 🟡 |
