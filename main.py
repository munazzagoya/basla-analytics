import html
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import Session

from database import (
    Article,
    ArticleSubject,
    DailyMetric,
    RealtimeSnapshot,
    SessionLocal,
    get_db,
    init_db,
)

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("basla-analytics")

# Environment configurations
GA4_PROPERTY_ID = os.getenv("GA4_PROPERTY_ID", "549617919").strip()
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
WORDPRESS_API_URL = os.getenv("WORDPRESS_API_URL", "https://your-wordpress-site.com/wp-json/wp/v2").strip()

# Active Multi-Property GA4 Configuration Map
GA4_PROPERTY_MAP: Dict[str, ArticleSubject] = {
    "549617919": ArticleSubject.BASLA,
    "557571893": ArticleSubject.LILLI,
    "556482058": ArticleSubject.MOLECULE,
}

# Default hostname map for canonical URL construction
PROPERTY_DEFAULT_HOSTS: Dict[str, str] = {
    "549617919": "baslasolutions.com",
    "557571893": "liliandco.shop",
    "556482058": "www.molecule.pk",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure database tables exist on API startup."""
    init_db()
    yield


# Initialize FastAPI app
app = FastAPI(
    title="Basla Analytics API",
    description="FastAPI service for Google Analytics 4 and WordPress content analytics",
    version="1.0.0",
    lifespan=lifespan,
)


# Helper: Check and obtain GA4 BetaAnalyticsDataClient
def get_ga4_client():
    """Initializes and returns a GA4 BetaAnalyticsDataClient if credentials are valid.
    Returns None if credentials or property ID are missing/invalid.
    """
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient

        creds_path = GOOGLE_APPLICATION_CREDENTIALS
        if creds_path and os.path.exists(creds_path):
            # Check if file has non-empty JSON
            try:
                with open(creds_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if not data or not isinstance(data, dict):
                        logger.warning(f"Credentials file '{creds_path}' is empty or invalid JSON.")
                        return None
                return BetaAnalyticsDataClient.from_service_account_file(creds_path)
            except Exception as e:
                logger.warning(f"Could not load service account from '{creds_path}': {e}")
                return None

        # Fallback to standard environment lookup
        return BetaAnalyticsDataClient()
    except Exception as e:
        logger.warning(f"Google Analytics Data Client could not be initialized: {e}")
        return None


# Helper: Validate whether a URL represents a genuine article
def is_valid_article(
    url: str,
    title: Optional[str] = None,
    subject: Optional[str] = None,
) -> bool:
    """Strictly segregates genuine articles from general site pages across BASLA, LILLI, and MOLECULE.

    Rules:
    1. BASLA: A URL is a valid article ONLY if it starts with '/insights/' AND contains
       a post slug (e.g., '/insights/onit-reporting-dashboards/'). Excludes exact root
       paths '/', '/insights/', and general non-article pages.
    2. LILLI & MOLECULE: A URL is a valid article ONLY if it represents a specific
       article/post slug. Excludes generic static/e-commerce paths (e.g., root '/',
       '/shop/', '/shop/page/*', '/product-category/*', '/size-guide/', '/cart/', '/checkout/', etc.).
    3. Strict Subject Isolation: URLs with known domains (baslasolutions.com, liliandco.shop,
       molecule.pk) are only valid for their respective subject.
    """
    if not url:
        return False

    u = url.strip()
    parsed = urlparse(u) if "://" in u else urlparse(f"https://dummy.com/{u.lstrip('/')}")
    path = parsed.path.rstrip("/")
    if not path:
        return False

    clean_path = path.lower()
    title_lower = (title or "").lower().strip()
    netloc = parsed.netloc.lower()

    # Determine active subject category
    subj = (subject or "").upper().strip()
    if not subj or subj == "ALL":
        if "molecule.pk" in netloc or "molecule" in clean_path:
            subj = "MOLECULE"
        elif "liliandco.shop" in netloc or "lilli" in clean_path:
            subj = "LILLI"
        elif "baslasolutions.com" in netloc or "/insights/" in clean_path:
            subj = "BASLA"
        else:
            subj = "BASLA"

    # Strict domain isolation
    if netloc:
        if subj == "BASLA" and ("liliandco.shop" in netloc or "molecule.pk" in netloc):
            return False
        if subj == "LILLI" and ("baslasolutions.com" in netloc or "molecule.pk" in netloc):
            return False
        if subj == "MOLECULE" and ("baslasolutions.com" in netloc or "liliandco.shop" in netloc):
            return False

    # Common exclusions across all subjects (pagination, author archives, 404s, home)
    if any(seg in clean_path for seg in ["/author/", "/page/", "/category/"]):
        return False
    if clean_path in ["/author", "/category", "/home", "/search"]:
        return False
    if title_lower:
        if any(term in title_lower for term in ["author", "category", "page not found", "404"]):
            return False
        if title_lower in ["home", "insights"]:
            return False

    # Subject 1: BASLA - Strictly /insights/<slug>
    if subj == "BASLA":
        if not clean_path.startswith("/insights/"):
            return False
        slug = clean_path[len("/insights/"):].strip("/")
        if not slug or slug in ["insights", "home"]:
            return False
        return True

    # Subject 2: LILLI - Specific article/product post slug, exclude static/e-commerce
    elif subj == "LILLI":
        static_prefixes = (
            "/shop",
            "/product-category",
            "/size-guide",
            "/cart",
            "/checkout",
            "/how-it-works",
            "/contact",
            "/about",
            "/faq",
            "/refund_returns",
            "/privacy-policy",
            "/terms",
        )
        if any(clean_path == sp or clean_path.startswith(sp + "/") for sp in static_prefixes):
            return False

        slug = clean_path.split("/")[-1]
        if not slug or slug in ["shop", "product", "home"]:
            return False
        return True

    # Subject 3: MOLECULE - Specific case study/product slug, exclude generic static/forms
    elif subj == "MOLECULE":
        static_prefixes = (
            "/home",
            "/projects",
            "/software-form",
            "/sector-a-contact-form",
            "/corporatechairscategories",
            "/shop",
            "/cart",
            "/checkout",
            "/contact",
            "/about",
            "/privacy-policy",
            "/terms",
            "/size-guide",
        )
        if any(clean_path == sp or clean_path.startswith(sp + "/") for sp in static_prefixes):
            return False

        slug = clean_path.split("/")[-1]
        if not slug or slug in ["home", "projects", "form"]:
            return False
        return True

    return False


# Helper: Tag subject from article URL slug, title, and categories/tags
def tag_subject(
    url: Optional[str] = None,
    title: Optional[str] = None,
    categories: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
) -> ArticleSubject:
    """Automatically tags subject based on URL host domain, slug, title, or category/tag keywords:
    - 'LILLI' if 'liliandco.shop' in URL
    - 'MOLECULE' if 'molecule.pk' in URL
    - 'BASLA' if 'baslasolutions.com' in URL
    - 'MOLECULE' if 'molecule' in slug/title/terms
    - 'LILLI' if 'lilli' or 'lili' in slug/title/terms
    - Default to 'BASLA'
    """
    url_lower = str(url or "").lower()
    if "liliandco.shop" in url_lower:
        return ArticleSubject.LILLI
    if "molecule.pk" in url_lower:
        return ArticleSubject.MOLECULE
    if "baslasolutions.com" in url_lower:
        return ArticleSubject.BASLA

    components: List[str] = []
    if url:
        components.append(str(url).lower())
    if title:
        components.append(str(title).lower())
    if categories:
        for cat in categories:
            if cat:
                components.append(str(cat).lower())
    if tags:
        for tag in tags:
            if tag:
                components.append(str(tag).lower())

    combined_text = " ".join(components)
    if "molecule" in combined_text:
        return ArticleSubject.MOLECULE
    elif "lilli" in combined_text or "lili" in combined_text:
        return ArticleSubject.LILLI
    else:
        return ArticleSubject.BASLA


# Helper: Parse datetime strings safely
def parse_iso_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        # Replace Z with UTC offset if present
        cleaned = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except Exception:
        return None


# Helper: Ensure an article exists in database before linking foreign keys and categorize it
def ensure_article_exists(
    db: Session,
    url: str,
    fallback_title: Optional[str] = None,
    categories: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    forced_subject: Optional[ArticleSubject] = None,
    host_name: Optional[str] = None,
) -> Article:
    clean = url.strip("/")

    # Determine canonical URL
    if url.startswith("http://") or url.startswith("https://"):
        target_url = url
    else:
        domain = host_name or "baslasolutions.com"
        target_url = f"https://{domain}/{clean}/" if clean else f"https://{domain}/"

    # 1. Exact match by target_url
    article = db.query(Article).filter(Article.url == target_url).first()

    # 2. Check if a synced article matches path without trailing slash
    if not article and clean:
        article = (
            db.query(Article).filter(Article.url.like(f"%/{clean}/")).first()
            or db.query(Article).filter(Article.url.like(f"%/{clean}")).first()
        )

    # 3. Check fallback match by input url
    if not article:
        article = db.query(Article).filter(Article.url == url).first()

    # Determine subject: forced_subject takes absolute priority
    if forced_subject:
        computed_subject = forced_subject
    else:
        computed_subject = tag_subject(url=target_url, title=fallback_title, categories=categories, tags=tags)

    target_title = fallback_title or (
        article.title if article and article.title and "page not found" not in article.title.lower()
        else (clean.replace("-", " ").title() if clean else f"Home - {computed_subject.value}")
    )

    if article:
        needs_commit = False
        # Update title if current title is placeholder or 404
        if fallback_title and (
            not article.title
            or "page not found" in article.title.lower()
            or article.title == "Home - Basla Solutions"
            or article.title == clean.replace("-", " ").title()
        ):
            article.title = fallback_title
            needs_commit = True

        # Always update subject if forced_subject is provided or subject mismatched
        if forced_subject and article.subject != forced_subject:
            article.subject = forced_subject
            article.last_updated = datetime.now(timezone.utc)
            needs_commit = True
        elif article.subject != computed_subject:
            article.subject = computed_subject
            article.last_updated = datetime.now(timezone.utc)
            needs_commit = True

        if needs_commit:
            db.commit()
            db.refresh(article)
        return article

    # 4. Create new article entry
    article = Article(
        url=target_url,
        title=target_title,
        subject=computed_subject,
        publish_date=None,
        last_updated=datetime.now(timezone.utc),
    )
    db.add(article)
    db.commit()
    db.refresh(article)
    return article


# 1. Fetch Realtime Data (Active Users, Top Pages, Country Breakdown)
def fetch_realtime_data(
    cache_minutes: int = 5,
    force: bool = False,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """Fetches active users, top pages, and country breakdown using GA4 BetaAnalyticsDataClient.
    Caches results in the SQLite `realtime_snapshot` table to respect GA4 quota limits.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        now_utc = datetime.now(timezone.utc)

        # Check SQLite cache first to respect GA4 quotas
        latest_snapshot = db.query(RealtimeSnapshot).order_by(RealtimeSnapshot.timestamp.desc()).first()
        if latest_snapshot and not force:
            snapshot_time = latest_snapshot.timestamp
            if snapshot_time.tzinfo is None:
                snapshot_time = snapshot_time.replace(tzinfo=timezone.utc)
            age_minutes = (now_utc - snapshot_time).total_seconds() / 60.0

            if age_minutes < cache_minutes:
                logger.info(f"Returning cached realtime snapshot ({age_minutes:.1f} mins old) to respect GA4 quota.")
                cached_records = (
                    db.query(RealtimeSnapshot)
                    .filter(RealtimeSnapshot.timestamp == latest_snapshot.timestamp)
                    .all()
                )
                return {
                    "source": "cache",
                    "timestamp": latest_snapshot.timestamp.isoformat(),
                    "total_active_users": sum(r.active_users for r in cached_records),
                    "snapshots": [
                        {
                            "page_title": r.page_title,
                            "active_users": r.active_users,
                            "views": r.views,
                            "country": r.country,
                        }
                        for r in cached_records
                    ],
                }

        # Initialize GA4 Client
        client = get_ga4_client()
        if not client or not GA4_PROPERTY_ID or GA4_PROPERTY_ID == "your_ga4_property_id_here":
            logger.warning("GA4 credentials or GA4_PROPERTY_ID not configured. Returning latest cached or empty result.")
            if latest_snapshot:
                cached_records = (
                    db.query(RealtimeSnapshot)
                    .filter(RealtimeSnapshot.timestamp == latest_snapshot.timestamp)
                    .all()
                )
                return {
                    "source": "cache_fallback",
                    "timestamp": latest_snapshot.timestamp.isoformat(),
                    "total_active_users": sum(r.active_users for r in cached_records),
                    "snapshots": [
                        {
                            "page_title": r.page_title,
                            "active_users": r.active_users,
                            "views": r.views,
                            "country": r.country,
                        }
                        for r in cached_records
                    ],
                }
            return {
                "source": "unconfigured",
                "timestamp": now_utc.isoformat(),
                "total_active_users": 0,
                "snapshots": [],
            }

        from google.analytics.data_v1beta.types import (
            Dimension,
            Metric,
            RunRealtimeReportRequest,
        )

        new_snapshots: List[RealtimeSnapshot] = []

        for prop_id, target_subject in GA4_PROPERTY_MAP.items():
            try:
                request = RunRealtimeReportRequest(
                    property=f"properties/{prop_id}",
                    dimensions=[
                        Dimension(name="unifiedScreenName"),
                        Dimension(name="country"),
                    ],
                    metrics=[
                        Metric(name="activeUsers"),
                    ],
                    limit=50,
                )
                response = client.run_realtime_report(request)
                if response.rows:
                    for row in response.rows:
                        page_title = row.dimension_values[0].value if len(row.dimension_values) > 0 else "Unknown"
                        country = row.dimension_values[1].value if len(row.dimension_values) > 1 else "Unknown"
                        active_users = int(row.metric_values[0].value) if len(row.metric_values) > 0 else 0

                        labeled_title = f"[{target_subject.value}] {page_title}"
                        snapshot = RealtimeSnapshot(
                            timestamp=now_utc,
                            active_users=active_users,
                            page_title=labeled_title,
                            views=active_users,
                            country=country,
                        )
                        db.add(snapshot)
                        new_snapshots.append(snapshot)
            except Exception as rt_err:
                logger.warning(f"Error querying realtime for Property {prop_id}: {rt_err}")

        if new_snapshots:
            db.commit()
            logger.info(f"Saved {len(new_snapshots)} new realtime snapshot records across active properties.")
        else:
            logger.info("GA4 realtime report returned 0 active users across all properties.")

        return {
            "source": "live_api",
            "timestamp": now_utc.isoformat(),
            "total_active_users": sum(s.active_users for s in new_snapshots),
            "snapshots": [
                {
                    "page_title": s.page_title,
                    "active_users": s.active_users,
                    "views": s.views,
                    "country": s.country,
                }
                for s in new_snapshots
            ],
        }

    except Exception as e:
        logger.error(f"Error fetching realtime GA4 data: {e}", exc_info=True)
        # Attempt fallback to cache
        latest_snapshot = db.query(RealtimeSnapshot).order_by(RealtimeSnapshot.timestamp.desc()).first()
        if latest_snapshot:
            cached_records = (
                db.query(RealtimeSnapshot)
                .filter(RealtimeSnapshot.timestamp == latest_snapshot.timestamp)
                .all()
            )
            return {
                "source": "cache_error_fallback",
                "timestamp": latest_snapshot.timestamp.isoformat(),
                "total_active_users": sum(r.active_users for r in cached_records),
                "snapshots": [
                    {
                        "page_title": r.page_title,
                        "active_users": r.active_users,
                        "views": r.views,
                        "country": r.country,
                    }
                    for r in cached_records
                ],
            }
        return {"source": "error", "error": str(e), "snapshots": [], "total_active_users": 0}
    finally:
        if close_db:
            db.close()


# 2. Fetch Article Metrics (Daily Metrics per URL)
def fetch_article_metrics(
    days: int = 3,
    force: bool = False,
    db: Optional[Session] = None,
) -> List[Dict[str, Any]]:
    """Fetches page views, totalUsers, userEngagementDuration, and bounceRate per URL from GA4.
    Upserts into the `daily_metrics` table and returns the metrics.
    Caches responses to avoid exceeding GA4 reporting tokens.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        now_utc = datetime.now(timezone.utc)
        start_date = (now_utc - timedelta(days=days)).date()

        # Check existing cached metrics in SQLite
        existing_metrics = (
            db.query(DailyMetric)
            .filter(DailyMetric.date >= start_date)
            .all()
        )

        if existing_metrics and not force:
            logger.info(f"Returning {len(existing_metrics)} cached daily metrics from database to respect GA4 quota.")
            return [
                {
                    "date": str(m.date),
                    "article_url": m.article_url,
                    "views": m.views,
                    "unique_readers": m.unique_readers,
                    "engagement_time": m.engagement_time,
                    "bounce_rate": m.bounce_rate,
                }
                for m in existing_metrics
            ]

        # Initialize GA4 client
        client = get_ga4_client()
        if not client:
            logger.warning("GA4 credentials not configured. Returning existing cached records.")
            existing_metrics = (
                db.query(DailyMetric)
                .filter(DailyMetric.date >= start_date)
                .all()
            )
            return [
                {
                    "date": str(m.date),
                    "article_url": m.article_url,
                    "views": m.views,
                    "unique_readers": m.unique_readers,
                    "engagement_time": m.engagement_time,
                    "bounce_rate": m.bounce_rate,
                }
                for m in existing_metrics
            ]

        from google.analytics.data_v1beta.types import (
            DateRange,
            Dimension,
            Metric,
            RunReportRequest,
        )

        all_saved_records: List[Dict[str, Any]] = []

        # Iterate across all configured GA4 Property IDs
        for prop_id, target_subject in GA4_PROPERTY_MAP.items():
            logger.info(f"Querying GA4 Property {prop_id} for subject {target_subject.value} (last {days} days)...")
            default_host = PROPERTY_DEFAULT_HOSTS.get(prop_id, "baslasolutions.com")

            request = RunReportRequest(
                property=f"properties/{prop_id}",
                date_ranges=[
                    DateRange(start_date=f"{days}daysAgo", end_date="yesterday")
                ],
                dimensions=[
                    Dimension(name="date"),
                    Dimension(name="hostName"),
                    Dimension(name="pagePath"),
                    Dimension(name="pageTitle"),
                ],
                metrics=[
                    Metric(name="screenPageViews"),
                    Metric(name="totalUsers"),
                    Metric(name="userEngagementDuration"),
                    Metric(name="bounceRate"),
                ],
                limit=10000,
            )

            try:
                response = client.run_report(request)
            except Exception as prop_err:
                logger.error(f"Error querying GA4 Property {prop_id} ({target_subject.value}): {prop_err}")
                continue

            if not response.rows:
                logger.info(f"GA4 Property {prop_id} returned 0 rows.")
                continue

            # Aggregate rows by (metric_date, canonical_article_url) to avoid unique constraint conflicts
            aggregated: Dict[tuple, Dict[str, Any]] = {}

            for row in response.rows:
                raw_date_str = row.dimension_values[0].value
                host_val = row.dimension_values[1].value
                page_path = row.dimension_values[2].value
                page_title = row.dimension_values[3].value if len(row.dimension_values) > 3 else None

                # Clean host and fallback if needed
                host_name = host_val.strip() if host_val and host_val not in ["(not set)", ""] else default_host

                try:
                    metric_date = datetime.strptime(raw_date_str, "%Y%m%d").date()
                except Exception:
                    metric_date = now_utc.date()

                views = int(row.metric_values[0].value) if row.metric_values[0].value else 0
                unique_readers = int(row.metric_values[1].value) if row.metric_values[1].value else 0
                engagement_time = float(row.metric_values[2].value) if row.metric_values[2].value else 0.0
                bounce_rate = float(row.metric_values[3].value) if row.metric_values[3].value else 0.0

                # Form canonical URL with host and path, preserving all page paths without /insights/ restriction
                clean_path = page_path.strip("/")
                canonical_url = f"https://{host_name}/{clean_path}/" if clean_path else f"https://{host_name}/"

                # Ensure article exists, forced to target_subject
                matched_article = ensure_article_exists(
                    db,
                    url=canonical_url,
                    fallback_title=page_title,
                    forced_subject=target_subject,
                    host_name=host_name,
                )
                canonical_article_url = matched_article.url

                key = (metric_date, canonical_article_url)
                if key not in aggregated:
                    aggregated[key] = {
                        "views": views,
                        "unique_readers": unique_readers,
                        "total_engagement": engagement_time * views,
                        "total_bounce": bounce_rate * views,
                        "subject": target_subject.value,
                    }
                else:
                    aggregated[key]["views"] += views
                    aggregated[key]["unique_readers"] += unique_readers
                    aggregated[key]["total_engagement"] += engagement_time * views
                    aggregated[key]["total_bounce"] += bounce_rate * views

            prop_saved_count = 0
            for (metric_date, canonical_article_url), agg_data in aggregated.items():
                tot_views = agg_data["views"]
                avg_engagement = (agg_data["total_engagement"] / tot_views) if tot_views > 0 else 0.0
                avg_bounce = (agg_data["total_bounce"] / tot_views) if tot_views > 0 else 0.0
                unique_readers = agg_data["unique_readers"]

                metric = (
                    db.query(DailyMetric)
                    .filter(DailyMetric.date == metric_date, DailyMetric.article_url == canonical_article_url)
                    .first()
                )

                if metric:
                    metric.views = tot_views
                    metric.unique_readers = unique_readers
                    metric.engagement_time = avg_engagement
                    metric.bounce_rate = avg_bounce
                else:
                    metric = DailyMetric(
                        date=metric_date,
                        article_url=canonical_article_url,
                        views=tot_views,
                        unique_readers=unique_readers,
                        engagement_time=avg_engagement,
                        bounce_rate=avg_bounce,
                    )
                    db.add(metric)

                all_saved_records.append({
                    "date": str(metric_date),
                    "article_url": canonical_article_url,
                    "views": tot_views,
                    "unique_readers": unique_readers,
                    "engagement_time": avg_engagement,
                    "bounce_rate": avg_bounce,
                    "subject": agg_data["subject"],
                })
                prop_saved_count += 1

            db.commit()
            logger.info(f"Property {prop_id} ({target_subject.value}): successfully synced {prop_saved_count} daily metric records.")

        logger.info(f"Multi-property GA4 sync complete: {len(all_saved_records)} total records persisted.")
        return all_saved_records

    except Exception as e:
        logger.error(f"Error fetching article metrics from GA4: {e}", exc_info=True)
        # Fallback to existing database records
        existing_metrics = (
            db.query(DailyMetric)
            .filter(DailyMetric.date >= (datetime.now(timezone.utc) - timedelta(days=days)).date())
            .all()
        )
        return [
            {
                "date": str(m.date),
                "article_url": m.article_url,
                "views": m.views,
                "unique_readers": m.unique_readers,
                "engagement_time": m.engagement_time,
                "bounce_rate": m.bounce_rate,
            }
            for m in existing_metrics
        ]
    finally:
        if close_db:
            db.close()


# 3. Sync WordPress Articles
def sync_wordpress_articles(
    per_page: int = 100,
    max_pages: Optional[int] = None,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """Calls WordPress REST API (/wp-json/wp/v2/posts) with _embed=wp:term,
    paginates through posts, and automatically tags subject as 'MOLECULE', 'LILLI',
    or default 'BASLA' based on article URL slug, title, and categories/tags.
    Upserts article records in the SQLite `articles` table.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        base_url = WORDPRESS_API_URL.rstrip("/")
        endpoint = base_url if base_url.endswith("/posts") else f"{base_url}/posts"

        logger.info(f"Calling WordPress REST API: {endpoint}")
        headers = {"User-Agent": "BaslaAnalytics/1.0"}

        page = 1
        synced_count = 0
        updated_count = 0
        total_processed = 0

        while True:
            params = {
                "per_page": min(per_page, 100),
                "page": page,
                "_embed": "wp:term",
                "_fields": "id,date,modified,link,slug,title,_links,_embedded",
            }

            try:
                response = requests.get(endpoint, params=params, headers=headers, timeout=20)
                if response.status_code == 400 and "rest_post_invalid_page_number" in response.text:
                    # End of pagination reached
                    break
                response.raise_for_status()
            except requests.exceptions.RequestException as e:
                logger.warning(f"Error fetching page {page} from WordPress API: {e}")
                if page == 1:
                    raise e
                break

            posts = response.json()
            if not isinstance(posts, list) or not posts:
                break

            for post in posts:
                link = post.get("link")
                if not link:
                    continue

                total_processed += 1
                raw_title = post.get("title", {})
                title_text = raw_title.get("rendered", "") if isinstance(raw_title, dict) else str(raw_title)
                clean_title = html.unescape(title_text).strip()
                slug = post.get("slug", "")

                publish_date = parse_iso_datetime(post.get("date"))
                last_updated = parse_iso_datetime(post.get("modified")) or datetime.now(timezone.utc)

                # Extract taxonomy terms (categories and tags)
                categories: List[str] = []
                tags: List[str] = []
                embedded_terms = post.get("_embedded", {}).get("wp:term", [])
                if isinstance(embedded_terms, list):
                    for term_group in embedded_terms:
                        if isinstance(term_group, list):
                            for term in term_group:
                                if isinstance(term, dict):
                                    tax = term.get("taxonomy", "")
                                    name = term.get("name", "")
                                    t_slug = term.get("slug", "")
                                    if tax == "category":
                                        if name:
                                            categories.append(name)
                                        if t_slug:
                                            categories.append(t_slug)
                                    else:
                                        if name:
                                            tags.append(name)
                                        if t_slug:
                                            tags.append(t_slug)

                # Tag subject: 'MOLECULE' -> 'LILLI' -> default 'BASLA'
                subject = tag_subject(
                    url=f"{link} {slug}",
                    title=clean_title,
                    categories=categories,
                    tags=tags,
                )

                article = db.query(Article).filter(Article.url == link).first()
                if article:
                    article.title = clean_title
                    article.subject = subject
                    article.publish_date = publish_date
                    article.last_updated = last_updated
                    updated_count += 1
                else:
                    article = Article(
                        url=link,
                        title=clean_title,
                        subject=subject,
                        publish_date=publish_date,
                        last_updated=last_updated,
                    )
                    db.add(article)
                    synced_count += 1

            db.commit()

            total_pages = int(response.headers.get("X-WP-TotalPages", 1))
            if max_pages and page >= max_pages:
                break
            if page >= total_pages:
                break
            page += 1

        logger.info(f"WordPress sync complete: {synced_count} added, {updated_count} updated across {total_processed} posts.")

        return {
            "status": "success",
            "added_count": synced_count,
            "updated_count": updated_count,
            "total_processed": total_processed,
        }

    except requests.exceptions.RequestException as e:
        logger.warning(f"Could not connect to WordPress API ({endpoint}): {e}")
        return {
            "status": "failed",
            "error": str(e),
            "message": "Failed to fetch from WordPress REST API endpoint.",
            "synced_count": 0,
        }
    except Exception as e:
        logger.error(f"Unexpected error syncing WordPress articles: {e}", exc_info=True)
        return {"status": "error", "error": str(e), "synced_count": 0}
    finally:
        if close_db:
            db.close()


# Helper: Reclassify all existing SQLite articles
def reclassify_all_articles(db: Optional[Session] = None) -> Dict[str, Any]:
    """Scans all articles in the SQLite database and updates their subject classification
    according to current URL slug and title tagging rules.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        articles = db.query(Article).all()
        updated_count = 0
        counts: Dict[str, int] = {}

        for article in articles:
            url_str = (article.url or "").lower()
            if "liliandco.shop" in url_str:
                new_subject = ArticleSubject.LILLI
            elif "molecule.pk" in url_str:
                new_subject = ArticleSubject.MOLECULE
            elif "baslasolutions.com" in url_str:
                new_subject = ArticleSubject.BASLA
            else:
                new_subject = tag_subject(url=article.url, title=article.title)

            if article.subject != new_subject:
                article.subject = new_subject
                article.last_updated = datetime.now(timezone.utc)
                updated_count += 1

            subj_str = new_subject.value if hasattr(new_subject, "value") else str(new_subject)
            counts[subj_str] = counts.get(subj_str, 0) + 1

        db.commit()
        logger.info(f"Reclassification complete: {updated_count} updated. Subject counts: {counts}")
        return {
            "total_articles": len(articles),
            "updated_count": updated_count,
            "counts": counts,
        }
    finally:
        if close_db:
            db.close()


# ---------------------------------------------------------
# FastAPI Endpoints
# ---------------------------------------------------------


@app.get("/health")
def health_check():
    return {
        "status": "online",
        "service": "Basla Analytics",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/realtime")
def get_realtime(
    cache_minutes: int = Query(5, description="Minutes to consider snapshot fresh"),
    force: bool = Query(False, description="Bypass cache and force GA4 call"),
    db: Session = Depends(get_db),
):
    """API endpoint to retrieve realtime analytics snapshot."""
    return fetch_realtime_data(cache_minutes=cache_minutes, force=force, db=db)


@app.get("/api/metrics")
def get_metrics(
    days: int = Query(3, description="Number of past days to fetch metrics for"),
    force: bool = Query(False, description="Bypass cache and force GA4 call"),
    db: Session = Depends(get_db),
):
    """API endpoint to retrieve daily article metrics."""
    return fetch_article_metrics(days=days, force=force, db=db)


@app.post("/api/sync-wordpress")
def post_sync_wordpress(
    per_page: int = Query(100, description="Max posts to sync"),
    db: Session = Depends(get_db),
):
    """API endpoint to trigger WordPress article synchronization."""
    result = sync_wordpress_articles(per_page=per_page, db=db)
    return result


@app.post("/api/reclassify")
def post_reclassify(db: Session = Depends(get_db)):
    """API endpoint to re-evaluate and update subject tags for all articles."""
    return reclassify_all_articles(db=db)


@app.get("/api/articles")
def list_articles(
    subject: Optional[str] = Query(None, description="Filter by subject"),
    only_valid: bool = Query(True, description="Filter to only genuine verified articles"),
    db: Session = Depends(get_db),
):
    """API endpoint to list articles tracked in the database."""
    query = db.query(Article)
    if subject:
        query = query.filter(Article.subject == subject.upper())
    articles = query.order_by(Article.last_updated.desc()).all()
    
    result = []
    for a in articles:
        subj_str = a.subject.value if hasattr(a.subject, "value") else str(a.subject)
        valid = is_valid_article(a.url, a.title, subj_str)
        if only_valid and not valid:
            continue
        result.append({
            "url": a.url,
            "title": a.title,
            "subject": subj_str,
            "is_valid_article": valid,
            "publish_date": a.publish_date.isoformat() if a.publish_date else None,
            "last_updated": a.last_updated.isoformat() if a.last_updated else None,
        })
    return result


if __name__ == "__main__":
    import sys

    print("==================================================")
    print("BASLA ANALYTICS - DATA INGESTION & CATEGORIZATION")
    print("==================================================")

    print("\n1. Initializing SQLite Database...")
    init_db()

    print("\n2. Syncing articles from WordPress REST API (all posts)...")
    wp_res = sync_wordpress_articles(per_page=100)
    print(f"WordPress Sync Result: {wp_res}")

    print("\n3. Fetching real metrics & articles from GA4 (last 30 days)...")
    met_res = fetch_article_metrics(days=30, force=True)
    print(f"GA4 Synced Daily Metric Records: {len(met_res)}")

    print("\n4. Re-categorizing all SQLite article records...")
    reclass_res = reclassify_all_articles()
    print(f"Article Re-classification Result: {reclass_res}")

    print("\n5. Fetching current GA4 Realtime Snapshot...")
    rt_res = fetch_realtime_data(force=True)
    print(f"Realtime Snapshot: active_users={rt_res.get('total_active_users')}, source={rt_res.get('source')}")

    print("\n==================================================")
    print("SYNC & CATEGORIZATION COMPLETE")
    print(f"Total Articles in DB: {reclass_res.get('total_articles')}")
    print(f"Database Subject Counts: {reclass_res.get('counts')}")
    print("==================================================")

    if "--server" in sys.argv:
        import uvicorn
        print("\nStarting FastAPI server on http://127.0.0.1:8000 ...")
        uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)

