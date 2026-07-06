"""Site config singleton: get-or-create."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.admin import SiteConfig

SITE_CONFIG_ID = 1


def get_or_create_site_config(db: Session) -> SiteConfig:
    cfg = db.get(SiteConfig, SITE_CONFIG_ID)
    if cfg is None:
        cfg = SiteConfig(id=SITE_CONFIG_ID)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg
