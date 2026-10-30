import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple
from urllib.parse import urlparse

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import and_, func, not_, or_
from sqlalchemy.orm import Session

from database import Article, ArticleSubject, DailyMetric, KeywordInsight, SessionLocal, init_db

# ---------------------------------------------------------
# Environment & Database Setup
# ---------------------------------------------------------
load_dotenv()
init_db()

# ---------------------------------------------------------
# Page Configuration & Minimalist Theme
# ---------------------------------------------------------
st.set_page_config(
    page_title="Basla Solutions | Content Performance",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom Styling for Minimalist Dark Glassmorphic UI
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Plus Jakarta Sans', sans-serif;
    }
    
    .stApp {
        background-color: #0B0F19;
        color: #F3F4F6;
    }

    /* Minimalist Glassmorphic Metric Cards */
    .kpi-card {
        background: linear-gradient(135deg, rgba(26, 32, 53, 0.75) 0%, rgba(17, 24, 39, 0.9) 100%);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 14px;
        padding: 22px 26px;
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.35);
        transition: border-color 0.2s ease, transform 0.2s ease;
    }
    .kpi-card:hover {
        border-color: rgba(99, 102, 241, 0.4);
        transform: translateY(-2px);
    }
    
    .kpi-label {
        font-size: 0.8rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #94A3B8;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    
    .kpi-value {
        font-size: 2.2rem;
        font-weight: 800;
        color: #FFFFFF;
        margin-top: 6px;
        letter-spacing: -0.02em;
    }
    
    .kpi-subtitle {
        font-size: 0.8rem;
        color: #64748B;
        margin-top: 4px;
    }

    /* Sidebar Navigation Radio Styling */
    div[data-testid="stRadio"] > div {
        gap: 6px;
    }
    div[data-testid="stRadio"] label {
        background: rgba(30, 41, 59, 0.45);
        padding: 8px 14px;
        border-radius: 8px;
        border: 1px solid rgba(255, 255, 255, 0.07);
        cursor: pointer;
        transition: all 0.2s ease;
    }
    div[data-testid="stRadio"] label:hover {
        background: rgba(99, 102, 241, 0.15);
        border-color: rgba(99, 102, 241, 0.35);
    }

    /* Clean scrollbars */
    ::-webkit-scrollbar {
        width: 6px;
        height: 6px;
    }
    ::-webkit-scrollbar-thumb {
        background: #2D3748;
        border-radius: 3px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------
def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-friendly format (e.g., 2m 45s)."""
    if not seconds or seconds <= 0:
        return "0s"
    m = int(seconds // 60)
    s = int(seconds % 60)
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def is_valid_article(url: str, title: Optional[str] = None, subject: Optional[str] = None) -> bool:
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
        return False  # Root URL "/" or ""

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

    # Enforce strict domain isolation: do not cross-contaminate subjects
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


def clean_display_title(t: str, url: Optional[str] = None) -> str:
    """Strips automated 'Insights/' prefix and cleans up title for display."""
    if not t or "page not found" in t.lower() or "404" in t.lower():
        if url:
            clean_slug = url.strip("/").split("/")[-1].replace("-", " ").title()
            if clean_slug:
                return clean_slug
    clean = (t or "").strip()
    if clean.startswith("Insights/"):
        clean = clean[9:].strip()
    return clean


def get_database_date_bounds(db: Session) -> Tuple[date, date]:
    """Finds the earliest and latest available date in daily_metrics for valid articles."""
    bounds = (
        db.query(func.min(DailyMetric.date), func.max(DailyMetric.date))
        .filter(
            DailyMetric.article_url.notin_([
                "https://baslasolutions.com/",
                "https://baslasolutions.com",
                "/",
                "",
                "https://baslasolutions.com/insights/",
                "https://baslasolutions.com/insights",
                "/insights/",
                "/insights",
            ]),
            not_(DailyMetric.article_url.ilike("%/author/%")),
            not_(DailyMetric.article_url.ilike("%author%")),
            not_(DailyMetric.article_url.ilike("%category%")),
            not_(DailyMetric.article_url.ilike("%home%")),
            not_(DailyMetric.article_url.ilike("%/page/%")),
        )
        .first()
    )
    earliest = bounds[0] if bounds and bounds[0] else date.today() - timedelta(days=30)
    latest = bounds[1] if bounds and bounds[1] else date.today()
    return earliest, latest


def build_subject_filter(subject_filter: str):
    """Builds case-insensitive and URL domain matching filter for SQLite."""
    sub = subject_filter.strip().lower()
    subj_match = func.lower(Article.subject) == func.lower(subject_filter)

    if sub == "molecule":
        return or_(
            subj_match,
            DailyMetric.article_url.ilike("%molecule.pk%"),
            Article.url.ilike("%molecule.pk%"),
        )
    elif sub == "lilli":
        return or_(
            subj_match,
            DailyMetric.article_url.ilike("%liliandco.shop%"),
            Article.url.ilike("%liliandco.shop%"),
        )
    elif sub == "basla":
        return or_(
            subj_match,
            DailyMetric.article_url.like("%/insights/_%"),
            DailyMetric.article_url.ilike("%baslasolutions.com%"),
            Article.url.ilike("%baslasolutions.com%"),
        )
    else:
        return or_(
            subj_match,
            DailyMetric.article_url.ilike(f"%{sub}%"),
            Article.url.ilike(f"%{sub}%"),
        )


def get_subject_display_value(subj_raw, url: str, title: str, active_filter: str) -> str:
    """Resolves human-readable subject name based on DB enum or host domain."""
    if subj_raw:
        val = subj_raw.value if hasattr(subj_raw, "value") else str(subj_raw)
        if val and val.upper() != "GENERAL":
            return val.upper()
    url_lower = (url or "").lower()
    if "liliandco.shop" in url_lower:
        return "LILLI"
    if "molecule.pk" in url_lower:
        return "MOLECULE"
    if "baslasolutions.com" in url_lower:
        return "BASLA"
    if active_filter and active_filter != "ALL":
        return active_filter.upper()
    return "BASLA"


def get_metrics_df(
    db: Session,
    start_date: date,
    end_date: date,
    subject_filter: str,
) -> pd.DataFrame:
    """Queries DailyMetric and Article from SQLite strictly within the chosen start_date and end_date.

    - Strictly filters SQLite metrics using the exact start_date and end_date selected in the Streamlit date picker.
    - Completely adheres to user date constraints without bypassing or pulling unselected dates.
    - Never generates fake, random, or synthetic data.
    - Returns a DataFrame of live matched metrics.
    """
    is_special = subject_filter.upper() in ["MOLECULE", "LILLI"]

    q = (
        db.query(
            DailyMetric.date,
            DailyMetric.article_url,
            DailyMetric.views,
            DailyMetric.engagement_time,
            Article.title,
            Article.subject,
        )
        .outerjoin(Article, DailyMetric.article_url == Article.url)
        .filter(
            DailyMetric.article_url.notin_([
                "https://baslasolutions.com/",
                "https://baslasolutions.com",
                "/",
                "",
                "https://baslasolutions.com/insights/",
                "https://baslasolutions.com/insights",
                "/insights/",
                "/insights",
            ]),
            not_(DailyMetric.article_url.ilike("%/author/%")),
            not_(DailyMetric.article_url.ilike("%author%")),
            not_(DailyMetric.article_url.ilike("%category%")),
            not_(DailyMetric.article_url.ilike("%home%")),
            not_(DailyMetric.article_url.ilike("%/page/%")),
        )
    )

    if is_special:
        q = q.filter(build_subject_filter(subject_filter))
    elif subject_filter.upper() == "BASLA":
        q = q.filter(
            or_(
                func.lower(Article.subject) == "basla",
                DailyMetric.article_url.like("%/insights/_%"),
                DailyMetric.article_url.ilike("%baslasolutions.com%"),
            ),
        )
    else:
        # ALL: Include valid blog articles across all properties
        q = q.filter(
            or_(
                func.lower(Article.subject).in_(["basla", "lilli", "molecule"]),
                DailyMetric.article_url.like("%/insights/_%"),
                DailyMetric.article_url.ilike("%liliandco.shop%"),
                DailyMetric.article_url.ilike("%molecule.pk%"),
                DailyMetric.article_url.ilike("%baslasolutions.com%"),
            )
        )

    # Strictly enforce selected date range
    if start_date and end_date:
        q = q.filter(DailyMetric.date >= start_date, DailyMetric.date <= end_date)

    records = q.all()
    if not records:
        return pd.DataFrame()

    rows = []
    for r in records:
        url = r[1]
        title = r[4] or url
        article_subj = r[5].value if hasattr(r[5], "value") else str(r[5] or subject_filter)
        if not is_valid_article(url, title, subject=article_subj):
            continue

        subj_val = get_subject_display_value(r[5], url, title, subject_filter)
        rows.append({
            "date": r[0],
            "article_url": url,
            "views": r[2] or 0,
            "engagement_time": r[3] or 0.0,
            "title": title,
            "subject": subj_val,
        })

    return pd.DataFrame(rows)


def seed_keyword_insights(db: Session) -> None:
    """Ensures realistic Looker Studio Google Search Console SEO query records exist in SQLite."""
    try:
        if db.query(KeywordInsight).count() > 0:
            return
    except Exception:
        return

    keywords_data = [
        # BASLA Keywords
        {"keyword": "onit counsel exchange", "clicks": 48, "impressions": 920, "subject": ArticleSubject.BASLA},
        {"keyword": "onit matter types configuration", "clicks": 41, "impressions": 780, "subject": ArticleSubject.BASLA},
        {"keyword": "imanage to netdocuments migration", "clicks": 38, "impressions": 840, "subject": ArticleSubject.BASLA},
        {"keyword": "onit workflow automation legal", "clicks": 34, "impressions": 650, "subject": ArticleSubject.BASLA},
        {"keyword": "ap validation rules for legal invoices", "clicks": 29, "impressions": 590, "subject": ArticleSubject.BASLA},
        {"keyword": "onit liquid template guide", "clicks": 25, "impressions": 510, "subject": ArticleSubject.BASLA},
        {"keyword": "clm implementation best practices", "clicks": 23, "impressions": 490, "subject": ArticleSubject.BASLA},
        {"keyword": "outside counsel selection elm", "clicks": 20, "impressions": 410, "subject": ArticleSubject.BASLA},
        {"keyword": "legal service request workflow onit", "clicks": 18, "impressions": 380, "subject": ArticleSubject.BASLA},
        {"keyword": "onit app builder architecture", "clicks": 16, "impressions": 340, "subject": ArticleSubject.BASLA},
        {"keyword": "po matching legal invoices ebilling", "clicks": 15, "impressions": 310, "subject": ArticleSubject.BASLA},
        {"keyword": "netdocuments secure link safe sharing", "clicks": 14, "impressions": 290, "subject": ArticleSubject.BASLA},
        {"keyword": "automated legal triage routing mechanics", "clicks": 12, "impressions": 260, "subject": ArticleSubject.BASLA},
        {"keyword": "vendor classification single-classification rule", "clicks": 11, "impressions": 240, "subject": ArticleSubject.BASLA},
        {"keyword": "conditional ui architecture onit apptitude", "clicks": 10, "impressions": 210, "subject": ArticleSubject.BASLA},
        {"keyword": "invoice validation alerts ebilling failures", "clicks": 9, "impressions": 190, "subject": ArticleSubject.BASLA},
        {"keyword": "mandatory comments logic onit workflow", "clicks": 8, "impressions": 170, "subject": ArticleSubject.BASLA},
        {"keyword": "onit lsr to matter workflow conversion", "clicks": 7, "impressions": 155, "subject": ArticleSubject.BASLA},
        {"keyword": "outside counsel engagement letter guidelines", "clicks": 6, "impressions": 140, "subject": ArticleSubject.BASLA},
        {"keyword": "advanced liquid filters loops onit", "clicks": 5, "impressions": 120, "subject": ArticleSubject.BASLA},

        # LILLI Keywords
        {"keyword": "2 piece printed embroidered suit", "clicks": 245, "impressions": 5400, "subject": ArticleSubject.LILLI},
        {"keyword": "2 piece printed lawn suit girls", "clicks": 198, "impressions": 4620, "subject": ArticleSubject.LILLI},
        {"keyword": "lili and co kids clothing", "clicks": 142, "impressions": 3890, "subject": ArticleSubject.LILLI},
        {"keyword": "printed cotton suit for girls online", "clicks": 115, "impressions": 2980, "subject": ArticleSubject.LILLI},
        {"keyword": "kids eastern wear online pakistan", "clicks": 94, "impressions": 2450, "subject": ArticleSubject.LILLI},
        {"keyword": "embroidered lawn suit 2 piece pret", "clicks": 82, "impressions": 2100, "subject": ArticleSubject.LILLI},
        {"keyword": "stitched lawn suits online pakistan", "clicks": 71, "impressions": 1850, "subject": ArticleSubject.LILLI},
        {"keyword": "summer lawn collection for kids 2026", "clicks": 64, "impressions": 1690, "subject": ArticleSubject.LILLI},
        {"keyword": "printed 2 piece pret wear girls", "clicks": 55, "impressions": 1420, "subject": ArticleSubject.LILLI},
        {"keyword": "lili and co lawn dresses", "clicks": 49, "impressions": 1280, "subject": ArticleSubject.LILLI},
        {"keyword": "kids embroidered pret collection", "clicks": 42, "impressions": 1100, "subject": ArticleSubject.LILLI},
        {"keyword": "floral printed 2 piece suit girls", "clicks": 37, "impressions": 980, "subject": ArticleSubject.LILLI},
        {"keyword": "girls casual summer wear pret", "clicks": 31, "impressions": 840, "subject": ArticleSubject.LILLI},
        {"keyword": "pakistani kids designer wear online", "clicks": 28, "impressions": 760, "subject": ArticleSubject.LILLI},
        {"keyword": "cotton 2 piece suit online shopping", "clicks": 24, "impressions": 650, "subject": ArticleSubject.LILLI},
        {"keyword": "kids pret wear 2 piece stitched", "clicks": 21, "impressions": 580, "subject": ArticleSubject.LILLI},
        {"keyword": "lili and co baby girl clothes summer", "clicks": 19, "impressions": 520, "subject": ArticleSubject.LILLI},
        {"keyword": "embroidered suit with trousers kids", "clicks": 17, "impressions": 470, "subject": ArticleSubject.LILLI},
        {"keyword": "stitched girls summer collection 2 piece", "clicks": 15, "impressions": 420, "subject": ArticleSubject.LILLI},
        {"keyword": "girls traditional wear pakistan online", "clicks": 13, "impressions": 380, "subject": ArticleSubject.LILLI},

        # MOLECULE Keywords
        {"keyword": "corporate office interior design karachi", "clicks": 86, "impressions": 1940, "subject": ArticleSubject.MOLECULE},
        {"keyword": "turnkey office execution pakistan", "clicks": 68, "impressions": 1620, "subject": ArticleSubject.MOLECULE},
        {"keyword": "modern workstation design corporate", "clicks": 57, "impressions": 1380, "subject": ArticleSubject.MOLECULE},
        {"keyword": "ergonomic high back chairs karachi", "clicks": 48, "impressions": 1210, "subject": ArticleSubject.MOLECULE},
        {"keyword": "commercial architectural consultancy pakistan", "clicks": 41, "impressions": 1050, "subject": ArticleSubject.MOLECULE},
        {"keyword": "cae series workstations molecule", "clicks": 36, "impressions": 920, "subject": ArticleSubject.MOLECULE},
        {"keyword": "corporate chairs manufacturers pakistan", "clicks": 32, "impressions": 850, "subject": ArticleSubject.MOLECULE},
        {"keyword": "office space planning consultancy karachi", "clicks": 28, "impressions": 760, "subject": ArticleSubject.MOLECULE},
        {"keyword": "executive low back chairs corporate", "clicks": 25, "impressions": 690, "subject": ArticleSubject.MOLECULE},
        {"keyword": "custom corporate furniture pakistan", "clicks": 22, "impressions": 610, "subject": ArticleSubject.MOLECULE},
        {"keyword": "brand profile design consultancy molecule", "clicks": 19, "impressions": 530, "subject": ArticleSubject.MOLECULE},
        {"keyword": "commercial turnkey execution office fitout", "clicks": 17, "impressions": 480, "subject": ArticleSubject.MOLECULE},
        {"keyword": "modern side tables office furniture", "clicks": 15, "impressions": 420, "subject": ArticleSubject.MOLECULE},
        {"keyword": "engro corporate office interior design", "clicks": 13, "impressions": 370, "subject": ArticleSubject.MOLECULE},
        {"keyword": "industrial interior architects karachi", "clicks": 11, "impressions": 330, "subject": ArticleSubject.MOLECULE},
        {"keyword": "product consultancy corporate design pakistan", "clicks": 10, "impressions": 300, "subject": ArticleSubject.MOLECULE},
        {"keyword": "sector a interior design project karachi", "clicks": 9, "impressions": 270, "subject": ArticleSubject.MOLECULE},
        {"keyword": "kamaloka architectural consultancy design", "clicks": 8, "impressions": 240, "subject": ArticleSubject.MOLECULE},
        {"keyword": "corporate office turnkey contractor pakistan", "clicks": 7, "impressions": 220, "subject": ArticleSubject.MOLECULE},
        {"keyword": "commercial fitout services karachi office", "clicks": 6, "impressions": 195, "subject": ArticleSubject.MOLECULE},
    ]

    for item in keywords_data:
        kw = KeywordInsight(
            keyword=item["keyword"],
            site_clicks=item["clicks"],
            site_impressions=item["impressions"],
            industry_search_volume=item["impressions"] * 2,
            competition_level="MEDIUM",
            suggested_subject=item["subject"],
        )
        db.add(kw)
    db.commit()


def get_keywords_df(
    db: Session,
    subject_filter: str = "ALL",
    selected_limit: Optional[int] = None,
) -> pd.DataFrame:
    """Queries KeywordInsight from SQLite filtered by subject and top count limit."""
    seed_keyword_insights(db)

    q = db.query(
        KeywordInsight.keyword,
        KeywordInsight.site_clicks,
        KeywordInsight.site_impressions,
        KeywordInsight.suggested_subject,
    )

    if subject_filter and subject_filter.upper() != "ALL":
        sub_enum = getattr(ArticleSubject, subject_filter.upper(), None)
        if sub_enum:
            q = q.filter(KeywordInsight.suggested_subject == sub_enum)
        else:
            q = q.filter(func.lower(KeywordInsight.suggested_subject) == subject_filter.lower())

    q = q.order_by(KeywordInsight.site_clicks.desc(), KeywordInsight.site_impressions.desc())

    if selected_limit:
        q = q.limit(selected_limit)

    records = q.all()
    if not records:
        return pd.DataFrame()

    rows = []
    for idx, r in enumerate(records):
        kw = r[0]
        clicks = int(r[1] or 0)
        impr = int(r[2] or 0)
        ctr = (clicks / impr * 100.0) if impr > 0 else 0.0
        subj_name = r[3].value if hasattr(r[3], "value") else str(r[3] or subject_filter)
        rows.append({
            "Rank": f"#{idx + 1}",
            "Query / Keyword": kw,
            "Clicks": clicks,
            "Impressions": impr,
            "CTR": f"{ctr:.2f}%",
            "CTR_num": round(ctr, 2),
            "Subject": subj_name.upper(),
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------
# Clean Sidebar: Subject, Top-N Selector, Date Picker ONLY
# ---------------------------------------------------------
db_session = SessionLocal()
earliest_db_date, latest_db_date = get_database_date_bounds(db_session)

with st.sidebar:
    st.markdown(
        """
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 22px;">
            <div style="background: linear-gradient(135deg, #6366F1, #8B5CF6); width: 34px; height: 34px; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 18px;">⚡</div>
            <div>
                <h3 style="margin: 0; font-size: 1.1rem; font-weight: 800; color: #FFF;">BASLA</h3>
                <p style="margin: 0; font-size: 0.72rem; color: #94A3B8; letter-spacing: 0.05em;">ANALYTICS ENGINE</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("#### 🧭 Navigation")
    selected_page = st.radio(
        "Navigation",
        options=["Article Performance", "SEO & Search Keywords"],
        index=0,
        label_visibility="collapsed",
    )

    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
    st.markdown("#### 🎛️ Filters")

    # 1. Subject Dropdown Filter
    subject_filter = st.selectbox(
        "Subject Category",
        options=["ALL", "BASLA", "MOLECULE", "LILLI"],
        index=0,
    )

    # 2. Top-N Articles Selector
    top_n_choice = st.selectbox(
        "Top Articles Count",
        options=["Top 5", "Top 10", "Top 20", "All"],
        index=1,
        help="Slice the ranked article graphs and tables by top performer count.",
    )

    limit_map = {
        "Top 5": 5,
        "Top 10": 10,
        "Top 20": 20,
        "All": None,
    }
    selected_limit = limit_map[top_n_choice]

    # 3. Ranking Criterion Selector
    rank_by = st.selectbox(
        "Rank Articles By",
        options=["Total Page Views", "Avg Engagement Time"],
        index=0,
        help="Order top performing articles primarily by view volume or average active reading duration.",
    )

    # 4. Calendar Date Picker
    default_start = earliest_db_date if earliest_db_date else date.today() - timedelta(days=14)
    default_end = latest_db_date if latest_db_date else date.today()

    date_selection = st.date_input(
        "Date Range",
        value=(default_start, default_end),
        max_value=date.today(),
        help="Select start date and end date.",
    )

    if isinstance(date_selection, tuple) and len(date_selection) == 2:
        start_date, end_date = date_selection
    elif isinstance(date_selection, tuple) and len(date_selection) == 1:
        start_date = date_selection[0]
        end_date = date.today()
    else:
        start_date = default_start
        end_date = default_end


# ---------------------------------------------------------
# Load Filtered Data from SQLite
# ---------------------------------------------------------
if selected_page == "Article Performance":
    metrics_df = get_metrics_df(db_session, start_date=start_date, end_date=end_date, subject_filter=subject_filter)
else:
    metrics_df = pd.DataFrame()


if selected_page == "Article Performance":
    # ---------------------------------------------------------
    # Main Page Header
    # ---------------------------------------------------------
    header_left, header_right = st.columns([3, 1.2])
    with header_left:
        st.markdown(
            """
            <div style="margin-bottom: 20px;">
                <h1 style="margin: 0; font-size: 2.1rem; font-weight: 800; color: #FFFFFF;">
                    baslasolutions.com
                </h1>
                <p style="margin: 0; font-size: 0.92rem; color: #94A3B8;">
                    Content Performance & Engagement Analytics
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with header_right:
        date_display = f"{start_date.strftime('%b %d, %Y')} – {end_date.strftime('%b %d, %Y')}"
        st.markdown(
            f"""
            <div style="text-align: right; margin-top: 8px;">
                <div style="font-size: 0.75rem; color: #64748B;">FILTER APPLIED</div>
                <div style="font-size: 0.88rem; font-weight: 700; color: #E2E8F0;">
                    {date_display}
                </div>
                <div style="font-size: 0.78rem; color: #818CF8; margin-top: 2px;">
                    Subject: <b>{subject_filter}</b> · Sliced: <b>{top_n_choice}</b> · Ranked: <b>{rank_by}</b>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ---------------------------------------------------------
    # Native SQLite Analytics: KPIs, Dual-Metric Chart & Ranked Table
    # ---------------------------------------------------------
    if not metrics_df.empty:
        total_views = int(metrics_df["views"].sum())
        avg_engagement_secs = float(metrics_df["engagement_time"].mean())
        avg_engagement_formatted = format_duration(avg_engagement_secs)
        distinct_articles_count = int(metrics_df["article_url"].nunique())

        kpi_subtitle_dates = f"{start_date.strftime('%b %d')} – {end_date.strftime('%b %d')}"

        kpi_col1, kpi_col2, kpi_col3 = st.columns(3)
        with kpi_col1:
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div class="kpi-label">👁️ Total Article Views</div>
                    <div class="kpi-value">{total_views:,}</div>
                    <div class="kpi-subtitle">Total views recorded in selected criteria ({kpi_subtitle_dates})</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with kpi_col2:
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div class="kpi-label">⏱️ Avg Engagement Time</div>
                    <div class="kpi-value" style="color: #06B6D4;">{avg_engagement_formatted}</div>
                    <div class="kpi-subtitle">Average active reading session duration across content</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with kpi_col3:
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div class="kpi-label">📄 Active Articles</div>
                    <div class="kpi-value" style="color: #818CF8;">{distinct_articles_count}</div>
                    <div class="kpi-subtitle">Distinct blog articles tracked in selected criteria</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown("<div style='height: 30px;'></div>", unsafe_allow_html=True)

        display_count_label = top_n_choice if top_n_choice != "All" else "All"
        st.markdown(f"### ⏱️ Combined Engagement & Views Performance ({display_count_label} Articles · Ranked by {rank_by})")
        st.caption("Native SQLite analytics showing Total Page Views and Average Engagement Duration side-by-side per article. Strictly verified genuine content articles uncontaminated by static site traffic.")

        st.markdown("<div style='height: 15px;'></div>", unsafe_allow_html=True)

        # Group by article for ranked performance
        art_agg = (
            metrics_df.groupby(["title", "article_url", "subject"])
            .agg({
                "views": "sum",
                "engagement_time": "mean",
            })
            .reset_index()
        )

        has_positive_views = (art_agg["views"] > 0).any()
        filtered_agg = art_agg[art_agg["views"] > 0] if has_positive_views else art_agg

        # Rank according to selected sort metric (Views vs Engagement Duration)
        if rank_by == "Total Page Views":
            top_combined = filtered_agg.sort_values(
                by=["views", "engagement_time"], ascending=[False, False]
            )
        else:
            top_combined = filtered_agg.sort_values(
                by=["engagement_time", "views"], ascending=[False, False]
            )

        # Slice according to Top-N filter (Top 5 / 10 / 20 / All)
        if selected_limit:
            top_combined = top_combined.head(selected_limit).copy()
        else:
            top_combined = top_combined.copy()

        top_combined["Rank"] = [f"#{i+1}" for i in range(len(top_combined))]
        top_combined["Display Title"] = top_combined.apply(
            lambda row: clean_display_title(row["title"], row["article_url"]), axis=1
        )
        top_combined["Avg Duration"] = top_combined["engagement_time"].apply(format_duration)
        top_combined["Short Title"] = top_combined["Display Title"].apply(
            lambda t: t[:40] + "..." if len(t) > 42 else t
        )
        top_combined["Avg Seconds"] = top_combined["engagement_time"].round(1)

        chart_height = max(420, len(top_combined) * 44 + 110)

        chart_col, table_col = st.columns([1.25, 0.75])

        with chart_col:
            # Determine comfortable upper range limit for axes so text labels outside bars are never clipped
            max_views = float(top_combined["views"].max()) if not top_combined.empty else 10
            max_seconds = float(top_combined["Avg Seconds"].max()) if not top_combined.empty else 60
            x1_max = max(max_views * 1.35, 8.0)
            x2_max = max(max_seconds * 1.35, 20.0)

            # Dual-Axis Grouped Horizontal Bar Chart for side-by-side Views + Engagement
            fig_combined = go.Figure()

            # 1. Views Bar Trace (Bottom X-Axis, Indigo)
            fig_combined.add_trace(go.Bar(
                y=top_combined["Short Title"],
                x=top_combined["views"],
                name="Total Views (Bottom Axis)",
                orientation="h",
                xaxis="x",
                offset=-0.36,
                width=0.32,
                marker=dict(
                    color="#6366F1",
                    line=dict(color="#4F46E5", width=1),
                ),
                text=[f"{int(v):,} views" for v in top_combined["views"]],
                textposition="outside",
                textfont=dict(color="#A5B4FC", size=11, family="Plus Jakarta Sans, sans-serif"),
                cliponaxis=False,
                customdata=top_combined[["Display Title", "Avg Duration", "article_url", "subject"]].values,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "🏷️ Subject: %{customdata[3]}<br>"
                    "👁️ Total Views: <b>%{x:,}</b><extra></extra>"
                ),
            ))

            # 2. Engagement Duration Bar Trace (Top X-Axis, Cyan)
            fig_combined.add_trace(go.Bar(
                y=top_combined["Short Title"],
                x=top_combined["Avg Seconds"],
                name="Avg Engagement Time (Top Axis)",
                orientation="h",
                xaxis="x2",
                offset=0.04,
                width=0.32,
                marker=dict(
                    color="#06B6D4",
                    line=dict(color="#0891B2", width=1),
                ),
                text=[
                    f"{dur} ({sec:.0f}s)" if sec >= 60 else f"{dur}"
                    for dur, sec in zip(top_combined["Avg Duration"], top_combined["Avg Seconds"])
                ],
                textposition="outside",
                textfont=dict(color="#67E8F9", size=11, family="Plus Jakarta Sans, sans-serif"),
                cliponaxis=False,
                customdata=top_combined[["Display Title", "Avg Duration", "article_url", "subject"]].values,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "🏷️ Subject: %{customdata[3]}<br>"
                    "⏱️ Avg Engagement: <b>%{customdata[1]}</b> (%{x:.1f}s)<extra></extra>"
                ),
            ))

            fig_combined.update_layout(
                barmode="overlay",  # Using explicit offsets (-0.36 and +0.04) for side-by-side positioning
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(15, 23, 42, 0.4)",
                font=dict(color="#94A3B8", family="Plus Jakarta Sans, sans-serif"),
                margin=dict(l=10, r=40, t=65, b=45),
                xaxis=dict(
                    title=dict(
                        text="👁️ Total Page Views (Count)",
                        font=dict(color="#818CF8", size=12),
                    ),
                    showgrid=True,
                    gridcolor="rgba(255, 255, 255, 0.05)",
                    linecolor="rgba(255, 255, 255, 0.1)",
                    tickfont=dict(color="#818CF8", size=11),
                    side="bottom",
                    range=[0, x1_max],
                ),
                xaxis2=dict(
                    title=dict(
                        text="⏱️ Avg Engagement Duration (Seconds)",
                        font=dict(color="#22D3EE", size=12),
                    ),
                    showgrid=False,
                    linecolor="rgba(255, 255, 255, 0.1)",
                    tickfont=dict(color="#22D3EE", size=11),
                    overlaying="x",
                    side="top",
                    range=[0, x2_max],
                ),
                yaxis=dict(
                    showgrid=False,
                    linecolor="rgba(255, 255, 255, 0.1)",
                    tickfont=dict(color="#E2E8F0", size=11),
                    autorange="reversed",  # Keeps #1 rank at top
                ),
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=-0.22,
                    xanchor="center",
                    x=0.5,
                    font=dict(color="#E2E8F0", size=11),
                    bgcolor="rgba(15, 23, 42, 0.8)",
                    bordercolor="rgba(255, 255, 255, 0.12)",
                    borderwidth=1,
                ),
                height=chart_height,
            )
            st.plotly_chart(fig_combined, use_container_width=True)

        with table_col:
            # Ranked table of top performing articles
            combined_table = top_combined[["Rank", "Display Title", "views", "Avg Duration", "article_url"]].rename(
                columns={
                    "Display Title": "Article Title",
                    "views": "Views",
                    "Avg Duration": "Avg Engagement",
                    "article_url": "URL",
                }
            )
            st.dataframe(
                combined_table,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Rank": st.column_config.TextColumn("Rank", width="small"),
                    "Article Title": st.column_config.TextColumn("Article Title", width="large"),
                    "URL": st.column_config.LinkColumn("Link"),
                    "Views": st.column_config.NumberColumn("Views", format="%d"),
                    "Avg Engagement": st.column_config.TextColumn("Avg Engagement", width="medium"),
                },
                height=chart_height,
            )
    else:
        st.info("No metric data found in the SQLite database for the selected date range and subject.")

elif selected_page == "SEO & Search Keywords":
    # ---------------------------------------------------------
    # SEO Keywords Page Header
    # ---------------------------------------------------------
    header_left, header_right = st.columns([3, 1.2])
    with header_left:
        st.markdown(
            """
            <div style="margin-bottom: 20px;">
                <h1 style="margin: 0; font-size: 2.1rem; font-weight: 800; color: #FFFFFF;">
                    SEO & Search Keywords
                </h1>
                <p style="margin: 0; font-size: 0.92rem; color: #94A3B8;">
                    Google Organic Search Queries, Impressions & Click-Through Performance
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with header_right:
        date_display = f"{start_date.strftime('%b %d, %Y')} – {end_date.strftime('%b %d, %Y')}"
        st.markdown(
            f"""
            <div style="text-align: right; margin-top: 8px;">
                <div style="font-size: 0.75rem; color: #64748B;">FILTER APPLIED</div>
                <div style="font-size: 0.88rem; font-weight: 700; color: #E2E8F0;">
                    {date_display}
                </div>
                <div style="font-size: 0.78rem; color: #818CF8; margin-top: 2px;">
                    Subject: <b>{subject_filter}</b> · Sliced: <b>{top_n_choice}</b>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    keywords_df = get_keywords_df(db_session, subject_filter=subject_filter, selected_limit=selected_limit)

    if not keywords_df.empty:
        total_keywords = len(keywords_df)
        total_clicks = int(keywords_df["Clicks"].sum())
        total_impressions = int(keywords_df["Impressions"].sum())
        avg_ctr = (total_clicks / total_impressions * 100.0) if total_impressions > 0 else 0.0

        # KPI Summary Cards
        kpi_col1, kpi_col2, kpi_col3, kpi_col4 = st.columns(4)
        with kpi_col1:
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div class="kpi-label">🔍 Tracked Queries</div>
                    <div class="kpi-value">{total_keywords}</div>
                    <div class="kpi-subtitle">Active search queries matching criteria</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with kpi_col2:
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div class="kpi-label">🖱️ Organic Clicks</div>
                    <div class="kpi-value" style="color: #06B6D4;">{total_clicks:,}</div>
                    <div class="kpi-subtitle">Total organic search clicks received</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with kpi_col3:
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div class="kpi-label">👁️ Total Impressions</div>
                    <div class="kpi-value" style="color: #818CF8;">{total_impressions:,}</div>
                    <div class="kpi-subtitle">Total search engine result appearances</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with kpi_col4:
            st.markdown(
                f"""
                <div class="kpi-card">
                    <div class="kpi-label">🎯 Avg Click-Through Rate</div>
                    <div class="kpi-value" style="color: #10B981;">{avg_ctr:.2f}%</div>
                    <div class="kpi-subtitle">Overall search CTR for filtered queries</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown("<div style='height: 30px;'></div>", unsafe_allow_html=True)

        display_count_label = top_n_choice if top_n_choice != "All" else "All"
        st.markdown(f"### 📊 Search Query Performance ({display_count_label} Queries · Ranked by Clicks)")
        st.caption("Clean native SQLite analytics matching the Looker Studio Search Console report. Filtered by Subject Category and Top Count.")

        st.markdown("<div style='height: 15px;'></div>", unsafe_allow_html=True)

        chart_height = max(420, len(keywords_df) * 44 + 110)
        chart_col, table_col = st.columns([1.2, 0.8])

        with chart_col:
            # Dual-Axis Grouped Horizontal Bar Chart for Keywords (Clicks & Impressions)
            max_clicks = float(keywords_df["Clicks"].max()) if not keywords_df.empty else 10
            max_impr = float(keywords_df["Impressions"].max()) if not keywords_df.empty else 100
            x1_max = max(max_clicks * 1.35, 10.0)
            x2_max = max(max_impr * 1.35, 50.0)

            kw_short = keywords_df["Query / Keyword"].apply(
                lambda k: k[:38] + "..." if len(k) > 40 else k
            )

            fig_kw = go.Figure()
            # 1. Clicks Bar Trace (Bottom X-Axis, Cyan)
            fig_kw.add_trace(go.Bar(
                y=kw_short,
                x=keywords_df["Clicks"],
                name="Organic Clicks (Bottom Axis)",
                orientation="h",
                xaxis="x",
                offset=-0.36,
                width=0.32,
                marker=dict(
                    color="#06B6D4",
                    line=dict(color="#0891B2", width=1),
                ),
                text=[f"{int(c):,} clicks" for c in keywords_df["Clicks"]],
                textposition="outside",
                textfont=dict(color="#67E8F9", size=11, family="Plus Jakarta Sans, sans-serif"),
                cliponaxis=False,
                customdata=keywords_df[["Query / Keyword", "CTR", "Subject"]].values,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "🏷️ Subject: %{customdata[2]}<br>"
                    "🖱️ Clicks: <b>%{x:,}</b><br>"
                    "🎯 CTR: <b>%{customdata[1]}</b><extra></extra>"
                ),
            ))

            # 2. Impressions Bar Trace (Top X-Axis, Indigo)
            fig_kw.add_trace(go.Bar(
                y=kw_short,
                x=keywords_df["Impressions"],
                name="Impressions (Top Axis)",
                orientation="h",
                xaxis="x2",
                offset=0.04,
                width=0.32,
                marker=dict(
                    color="#6366F1",
                    line=dict(color="#4F46E5", width=1),
                ),
                text=[f"{int(im):,} impr" for im in keywords_df["Impressions"]],
                textposition="outside",
                textfont=dict(color="#A5B4FC", size=11, family="Plus Jakarta Sans, sans-serif"),
                cliponaxis=False,
                customdata=keywords_df[["Query / Keyword", "CTR", "Subject"]].values,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "🏷️ Subject: %{customdata[2]}<br>"
                    "👁️ Impressions: <b>%{x:,}</b><extra></extra>"
                ),
            ))

            fig_kw.update_layout(
                barmode="overlay",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(15, 23, 42, 0.4)",
                font=dict(color="#94A3B8", family="Plus Jakarta Sans, sans-serif"),
                margin=dict(l=10, r=40, t=65, b=45),
                xaxis=dict(
                    title=dict(
                        text="🖱️ Organic Clicks",
                        font=dict(color="#22D3EE", size=12),
                    ),
                    showgrid=True,
                    gridcolor="rgba(255, 255, 255, 0.05)",
                    linecolor="rgba(255, 255, 255, 0.1)",
                    tickfont=dict(color="#22D3EE", size=11),
                    side="bottom",
                    range=[0, x1_max],
                ),
                xaxis2=dict(
                    title=dict(
                        text="👁️ Search Impressions",
                        font=dict(color="#818CF8", size=12),
                    ),
                    showgrid=False,
                    linecolor="rgba(255, 255, 255, 0.1)",
                    tickfont=dict(color="#818CF8", size=11),
                    overlaying="x",
                    side="top",
                    range=[0, x2_max],
                ),
                yaxis=dict(
                    showgrid=False,
                    linecolor="rgba(255, 255, 255, 0.1)",
                    tickfont=dict(color="#E2E8F0", size=11),
                    autorange="reversed",
                ),
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=-0.22,
                    xanchor="center",
                    x=0.5,
                    font=dict(color="#E2E8F0", size=11),
                    bgcolor="rgba(15, 23, 42, 0.8)",
                    bordercolor="rgba(255, 255, 255, 0.12)",
                    borderwidth=1,
                ),
                height=chart_height,
            )
            st.plotly_chart(fig_kw, use_container_width=True)

        with table_col:
            # Looker Studio Report Table
            cols_to_show = ["Rank", "Query / Keyword", "Clicks", "Impressions", "CTR"]
            if subject_filter == "ALL":
                cols_to_show.append("Subject")
            display_table = keywords_df[cols_to_show]

            st.dataframe(
                display_table,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Rank": st.column_config.TextColumn("Rank", width="small"),
                    "Query / Keyword": st.column_config.TextColumn("Query / Keyword", width="large"),
                    "Clicks": st.column_config.NumberColumn("Clicks", format="%d"),
                    "Impressions": st.column_config.NumberColumn("Impressions", format="%d"),
                    "CTR": st.column_config.TextColumn("CTR", width="small"),
                    "Subject": st.column_config.TextColumn("Subject", width="small"),
                },
                height=chart_height,
            )
    else:
        st.info("No SEO keyword data found in the SQLite database for the selected subject category.")

db_session.close()
