import re
from collections import Counter
from datetime import datetime

import requests
import streamlit as st
from kiwipiepy import Kiwi


st.set_page_config(
    page_title="댓글 갈림길",
    page_icon="🛤️",
    layout="wide",
)

API_URL = "https://www.googleapis.com/youtube/v3"
MAX_COMMENTS = 500

# 추천 검색어로 쓰기에는 의미가 약한 단어들
STOPWORDS = {
    "영상", "유튜브", "댓글", "채널", "사람", "생각", "내용", "부분",
    "진짜", "정말", "그냥", "너무", "지금", "이번", "오늘", "우리",
    "제가", "저는", "이거", "저거", "그거", "이것", "저것", "그것",
    "정도", "때문", "관련", "경우", "이야기", "설명", "감사", "ㅋㅋ",
    "ㅎㅎ", "하는", "있는", "없는", "같은", "좋은", "보고", "보면",
}


@st.cache_resource
def get_kiwi():
    return Kiwi()


def extract_video_id(value: str):
    """일반 주소, 단축 주소, Shorts 주소 또는 11자리 영상 ID를 처리한다."""
    value = value.strip()
    patterns = [
        r"(?:youtube\.com/watch\?[^\s]*v=)([A-Za-z0-9_-]{11})",
        r"(?:youtu\.be/)([A-Za-z0-9_-]{11})",
        r"(?:youtube\.com/shorts/)([A-Za-z0-9_-]{11})",
        r"^([A-Za-z0-9_-]{11})$",
    ]
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return match.group(1)
    return None


def youtube_get(api_key: str, resource: str, **params):
    """YouTube Data API를 호출하고 사용자가 이해할 수 있는 오류를 반환한다."""
    try:
        response = requests.get(
            f"{API_URL}/{resource}",
            params={**params, "key": api_key},
            timeout=30,
        )
        data = response.json()
    except requests.RequestException as exc:
        raise RuntimeError("유튜브에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.") from exc
    except ValueError as exc:
        raise RuntimeError("유튜브의 응답을 읽지 못했습니다.") from exc

    if response.status_code != 200:
        error = data.get("error", {})
        reasons = [item.get("reason", "") for item in error.get("errors", [])]

        if "commentsDisabled" in reasons:
            message = "이 영상은 댓글 기능이 꺼져 있습니다."
        elif "quotaExceeded" in reasons or "dailyLimitExceeded" in reasons:
            message = "오늘의 YouTube API 사용 한도를 초과했습니다."
        elif "videoNotFound" in reasons:
            message = "영상을 찾을 수 없습니다. 주소를 확인해 주세요."
        elif "keyInvalid" in reasons:
            message = "YouTube API 키가 올바르지 않습니다."
        else:
            message = error.get("message", f"YouTube API 오류({response.status_code})")
        raise RuntimeError(message)

    return data


def all_items(api_key: str, resource: str, **params):
    """nextPageToken을 따라가며 모든 페이지의 항목을 가져온다."""
    while True:
        data = youtube_get(api_key, resource, **params)
        yield from data.get("items", [])
        next_token = data.get("nextPageToken")
        if not next_token:
            break
        params["pageToken"] = next_token


def extract_nouns(text: str):
    """한국어 일반명사·고유명사와 영문 단어를 추출한다."""
    words = []
    tokens = list(get_kiwi().tokenize(str(text)))
    index = 0

    while index < len(tokens):
        token = tokens[index]

        # 예: 초(XPN)+전도체(NNG) → 초전도체
        if (
            token.tag == "XPN"
            and index + 1 < len(tokens)
            and tokens[index + 1].tag in {"NNG", "NNP"}
            and token.start + token.len == tokens[index + 1].start
        ):
            word = token.form + tokens[index + 1].form
            index += 2
        elif token.tag in {"NNG", "NNP"}:
            word = token.form
            index += 1
        elif token.tag == "SL":
            word = token.form.lower()
            index += 1
        else:
            index += 1
            continue

        # 예: 상용(NNG)+화(XSN) → 상용화
        while (
            index < len(tokens)
            and tokens[index].tag == "XSN"
            and tokens[index - 1].start + tokens[index - 1].len == tokens[index].start
        ):
            word += tokens[index].form
            index += 1

        word = word.strip()
        if len(word) >= 2:
            words.append(word)
    return words


def select_comment_keywords(comments, limit=5):
    """한 댓글 안의 중복 단어는 한 번만 세어 핵심 관심사 5개를 고른다."""
    counts = Counter()
    for comment in comments:
        unique_words = {
            word
            for word in extract_nouns(comment["text"])
            if word not in STOPWORDS and not word.isdigit()
        }
        counts.update(unique_words)
    return counts.most_common(limit)


@st.cache_data(ttl=600, show_spinner=False)
def analyze_video(_api_key: str, video_id: str):
    """영상 정보와 최대 500개의 공개 원댓글·대댓글을 가져온다."""
    video_response = youtube_get(
        _api_key,
        "videos",
        part="snippet,statistics",
        id=video_id,
    )
    if not video_response.get("items"):
        raise RuntimeError("영상을 찾을 수 없습니다. 주소를 확인해 주세요.")

    item = video_response["items"][0]
    snippet = item["snippet"]
    stats = item.get("statistics", {})
    thumbnails = snippet.get("thumbnails", {})
    thumbnail = (
        thumbnails.get("high")
        or thumbnails.get("medium")
        or thumbnails.get("default")
        or {}
    ).get("url", "")

    video = {
        "id": video_id,
        "title": snippet.get("title", ""),
        "channel": snippet.get("channelTitle", ""),
        "category_id": snippet.get("categoryId", ""),
        "thumbnail": thumbnail,
        "views": int(stats.get("viewCount", 0)),
        "likes": int(stats.get("likeCount", 0)),
        "comment_count": int(stats.get("commentCount", 0)),
    }

    comments = {}
    for thread in all_items(
        _api_key,
        "commentThreads",
        part="snippet",
        videoId=video_id,
        maxResults=100,
        order="relevance",
        textFormat="plainText",
    ):
        top_comment = thread["snippet"]["topLevelComment"]
        top_snippet = top_comment["snippet"]
        comments[top_comment["id"]] = {
            "text": top_snippet.get("textDisplay", ""),
            "likes": int(top_snippet.get("likeCount", 0)),
            "type": "원댓글",
        }

        if len(comments) >= MAX_COMMENTS:
            break

        if thread["snippet"].get("totalReplyCount", 0) > 0:
            for reply in all_items(
                _api_key,
                "comments",
                part="snippet",
                parentId=top_comment["id"],
                maxResults=100,
                textFormat="plainText",
            ):
                reply_snippet = reply["snippet"]
                comments[reply["id"]] = {
                    "text": reply_snippet.get("textDisplay", ""),
                    "likes": int(reply_snippet.get("likeCount", 0)),
                    "type": "대댓글",
                }
                if len(comments) >= MAX_COMMENTS:
                    break

        if len(comments) >= MAX_COMMENTS:
            break

    comment_list = list(comments.values())
    keywords = select_comment_keywords(comment_list)
    return video, comment_list, keywords


def build_search_query(title: str, selected_keyword: str):
    """원본 제목의 핵심어와 사용자가 선택한 관심사를 조합한다."""
    title_words = []
    for word in extract_nouns(title):
        if word not in STOPWORDS and word != selected_keyword and word not in title_words:
            title_words.append(word)
    parts = [selected_keyword] + title_words[:2]
    return " ".join(parts)


def iso_duration_to_text(duration: str):
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return "길이 확인 불가"
    hours, minutes, seconds = [int(value or 0) for value in match.groups()]
    if hours:
        return f"{hours}시간 {minutes}분"
    return f"{minutes}분 {seconds}초"


@st.cache_data(ttl=600, show_spinner=False)
def recommend_videos(
    _api_key: str,
    original_video_id: str,
    query: str,
    category_id: str,
):
    """선택한 댓글 관심사와 관련된 한국어 영상 5개를 추천한다."""
    search_params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": 12,
        "order": "relevance",
        "relevanceLanguage": "ko",
        "regionCode": "KR",
        "safeSearch": "moderate",
    }
    if category_id:
        search_params["videoCategoryId"] = category_id

    search_data = youtube_get(_api_key, "search", **search_params)
    ids = [
        item["id"]["videoId"]
        for item in search_data.get("items", [])
        if item.get("id", {}).get("videoId") != original_video_id
    ]
    if not ids:
        return []

    detail_data = youtube_get(
        _api_key,
        "videos",
        part="snippet,statistics,contentDetails,status",
        id=",".join(ids),
    )

    videos_by_id = {}
    for item in detail_data.get("items", []):
        if item.get("status", {}).get("privacyStatus") != "public":
            continue
        snippet = item["snippet"]
        stats = item.get("statistics", {})
        thumbs = snippet.get("thumbnails", {})
        thumbnail = (
            thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}
        ).get("url", "")
        videos_by_id[item["id"]] = {
            "id": item["id"],
            "title": snippet.get("title", ""),
            "channel": snippet.get("channelTitle", ""),
            "thumbnail": thumbnail,
            "views": int(stats.get("viewCount", 0)),
            "duration": iso_duration_to_text(item.get("contentDetails", {}).get("duration", "")),
        }

    # 검색 API가 정한 관련성 순서를 유지한다.
    return [videos_by_id[video_id] for video_id in ids if video_id in videos_by_id][:5]


def reset_analysis():
    for key in (
        "analysis",
        "analyzed_url",
        "recommendations",
        "recommendation_keyword",
        "search_query",
    ):
        st.session_state.pop(key, None)


st.title("🛤️ 댓글 갈림길")
st.write(
    "영상 댓글에서 많이 언급된 관심사를 발견하고, "
    "내가 선택한 관심사와 관련된 다음 영상을 추천받아 보세요."
)

with st.expander("이 서비스는 어떻게 추천하나요?"):
    st.write(
        "원댓글과 대댓글에서 자주 등장한 명사를 찾은 뒤, 사용자가 선택한 단어와 "
        "원본 영상 제목의 핵심어를 조합해 관련 영상을 검색합니다. "
        "유튜브 개인 추천 기록은 사용하지 않습니다."
    )

url = st.text_input(
    "분석할 유튜브 영상 주소",
    placeholder="https://youtu.be/영상ID",
    on_change=reset_analysis,
)

if st.button("댓글 관심사 찾기", type="primary", use_container_width=True):
    video_id = extract_video_id(url)
    if not video_id:
        st.error("올바른 유튜브 영상 주소를 입력해 주세요.")
    else:
        try:
            api_key = st.secrets["YOUTUBE_API_KEY"]
            with st.spinner("영상 댓글에서 관심사를 찾고 있습니다..."):
                analysis = analyze_video(api_key, video_id)
            for key in ("recommendations", "recommendation_keyword", "search_query"):
                st.session_state.pop(key, None)
            st.session_state["analysis"] = analysis
            st.session_state["analyzed_url"] = url
        except (KeyError, FileNotFoundError):
            st.error("Streamlit Secrets에 YOUTUBE_API_KEY를 등록해 주세요.")
        except RuntimeError as exc:
            st.error(str(exc))


if "analysis" in st.session_state:
    video, comments, keywords = st.session_state["analysis"]

    st.divider()
    image_col, info_col = st.columns([1, 2])
    with image_col:
        if video["thumbnail"]:
            st.image(video["thumbnail"], use_container_width=True)
    with info_col:
        st.subheader(video["title"])
        st.write(f"채널: **{video['channel']}**")
        metric1, metric2, metric3 = st.columns(3)
        metric1.metric("조회 수", f"{video['views']:,}")
        metric2.metric("공개 댓글 수", f"{video['comment_count']:,}")
        metric3.metric("분석한 댓글", f"{len(comments):,}")

    if not comments:
        st.warning("분석할 수 있는 공개 댓글이 없습니다.")
    elif not keywords:
        st.warning("추천에 사용할 핵심 단어를 찾지 못했습니다.")
    else:
        st.subheader("1. 댓글에서 발견한 관심사")
        st.caption("숫자는 해당 단어가 포함된 댓글 수입니다. 한 댓글의 중복 단어는 한 번만 셉니다.")

        keyword_labels = [f"{word} ({count})" for word, count in keywords]
        selected_label = st.radio(
            "더 알아보고 싶은 관심사를 선택하세요.",
            keyword_labels,
            horizontal=True,
        )
        selected_index = keyword_labels.index(selected_label)
        selected_keyword = keywords[selected_index][0]

        st.subheader("2. 선택한 관심사의 다음 영상")
        if st.button("관련 영상 추천받기", use_container_width=True):
            try:
                api_key = st.secrets["YOUTUBE_API_KEY"]
                search_query = build_search_query(video["title"], selected_keyword)
                with st.spinner("관련 영상을 찾고 있습니다..."):
                    recommendations = recommend_videos(
                        api_key,
                        video["id"],
                        search_query,
                        video["category_id"],
                    )

                st.session_state["recommendations"] = recommendations
                st.session_state["recommendation_keyword"] = selected_keyword
                st.session_state["search_query"] = search_query
            except RuntimeError as exc:
                st.error(str(exc))

        if (
            st.session_state.get("recommendation_keyword") == selected_keyword
            and "recommendations" in st.session_state
        ):
            recommendations = st.session_state["recommendations"]
            query = st.session_state["search_query"]
            st.info(
                f"댓글 관심사 **‘{selected_keyword}’**와 원본 영상의 주제를 조합한 "
                f"**‘{query}’**를 기준으로 추천했습니다."
            )

            if not recommendations:
                st.warning("조건에 맞는 관련 영상을 찾지 못했습니다. 다른 관심사를 선택해 보세요.")
            else:
                for number, recommendation in enumerate(recommendations, start=1):
                    with st.container(border=True):
                        thumb_col, detail_col = st.columns([1, 2])
                        with thumb_col:
                            if recommendation["thumbnail"]:
                                st.image(recommendation["thumbnail"], use_container_width=True)
                        with detail_col:
                            st.markdown(f"#### {number}. {recommendation['title']}")
                            st.write(f"채널: **{recommendation['channel']}**")
                            st.write(
                                f"조회 수 **{recommendation['views']:,}회** · "
                                f"영상 길이 **{recommendation['duration']}**"
                            )
                            st.link_button(
                                "유튜브에서 보기",
                                f"https://www.youtube.com/watch?v={recommendation['id']}",
                            )

st.divider()
st.caption(
    f"최대 {MAX_COMMENTS}개의 공개 댓글을 분석합니다. 삭제·숨김 댓글은 포함되지 않으며, "
    f"추천 결과는 {datetime.now().strftime('%Y-%m-%d')} 기준 YouTube 검색 결과에 따라 달라질 수 있습니다."
)
