# Bug 修复日志

记录所有 bug 修复，包括问题描述、根因分析和修复方案。

---

## [BUG-001] 素材库删除素材返回 INTERNAL_ERROR

- **日期**: 2026-04-30
- **现象**: 在素材库删除素材时，接口返回 `{"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred"}}`
- **根因**: `delete_asset` 端点只清理了 `task_assets` 表的关联记录，未清理 `asset_analysis` 表。SQLAlchemy 通过 `backref="analysis"` 关系尝试将 `asset_analysis.asset_id` 设为 `NULL`，但该列有 `NOT NULL` 约束，导致 `IntegrityError`。
- **修复文件**: `app/routers/assets.py` — `delete_asset` 函数
- **修复方案**: 在删除 Asset 之前，先执行 `db.query(AssetAnalysis).filter(AssetAnalysis.asset_id == asset_id).delete()` 清理关联的分析记录。
- **验证**: 删除接口返回 204，数据库和文件系统均正确清理。
