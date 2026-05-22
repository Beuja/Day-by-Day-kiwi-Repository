import re
import logging
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from kiwipiepy import Kiwi

logger = logging.getLogger(__name__)

# ==========================================
# 1. 감정 분석 사전 데이터 (내장형으로 경로 차단)
# ==========================================
EMOTION_KEYS = ("joy", "sadness", "anger", "fear", "trust", "surprise")

DEFAULT_NRC_LEXICON = {
    "행복": {"joy": 1.0, "trust": 0.6},
    "기쁨": {"joy": 1.0, "surprise": 0.3},
    "즐겁": {"joy": 0.9},
    "신남": {"joy": 0.7, "surprise": 0.5},
    "슬픔": {"sadness": 1.0},
    "우울": {"sadness": 0.9, "fear": 0.2},
    "불안": {"fear": 0.8, "sadness": 0.2},
    "무섭": {"fear": 1.0},
    "화나": {"anger": 1.0},
    "분노": {"anger": 1.0},
    "짜증": {"anger": 0.8, "sadness": 0.2},
    "믿음": {"trust": 1.0},
    "신뢰": {"trust": 1.0},
    "놀람": {"surprise": 1.0},
    "당황": {"surprise": 0.7, "fear": 0.3},
}

DEFAULT_KNU_LEXICON = {
    "편안": {"trust": 0.5, "joy": 0.3},
    "안정": {"trust": 0.7},
    "만족": {"joy": 0.7, "trust": 0.3},
    "외롭": {"sadness": 0.8},
    "불쾌": {"anger": 0.4, "sadness": 0.6},
    "긴장": {"fear": 0.6, "surprise": 0.2},
}

# ==========================================
# 2. Kiwi 형태소 기반 감정 분석 엔진
# ==========================================
class EmotionAnalyzer:
    def __init__(self):
        # C++ Kiwi 형태소 기동 (전용 서버이므로 에러 시 크래시 처리)
        self.kiwi = Kiwi()
        self.nrc_lexicon = DEFAULT_NRC_LEXICON
        self.knu_lexicon = DEFAULT_KNU_LEXICON

    def analyze(self, text: str) -> dict:
        tokens = self.tokenize_and_filter(text)
        if not tokens:
            return self._empty_emotions()

        scores = self._zero_scores()
        matched_count = 0

        for token in tokens:
            token_scores = self._lookup_token_scores(token)
            if self._has_signal(token_scores):
                matched_count += 1
                self._accumulate(scores, token_scores)

        # 6차원 스코어 정규화
        normalized = self._normalize_scores(scores)
        
        # 2차원 valence & arousal 계산
        valence = self._compute_valence(normalized)
        arousal = self._compute_arousal(normalized)
        primary_emotion = self._primary_emotion(normalized)

        return {
            **normalized,
            'valence': valence,
            'arousal': arousal,
            'primary_emotion': primary_emotion,
        }

    def tokenize_and_filter(self, text: str) -> list[str]:
        raw_tokens = self.kiwi.tokenize(text)
        filtered = []
        for tok in raw_tokens:
            word = tok.form.strip().lower()
            tag = tok.tag

            if not word:
                continue
            if self._is_filtered_pos(tag):
                continue
            if self._is_meaningless_token(word):
                continue
            filtered.append(word)
        return filtered

    def _is_filtered_pos(self, tag: str) -> bool:
        filtered_prefixes = ("J", "E", "X", "S", "W")
        filtered_exact = {
            "SF", "SP", "SS", "SE", "SO", "SW", "SH", "SN", "NR", "NP",
            "W_URL", "W_EMAIL", "W_HASHTAG", "W_MENTION", "W_SERIAL",
        }
        if tag in filtered_exact or tag.startswith(filtered_prefixes):
            return True
        if tag in {"MM", "IC"}:
            return True
        return False

    def _is_meaningless_token(self, token: str) -> bool:
        if len(token) <= 1:
            return True
        stopwords = {
            "그리고", "그래서", "하지만", "근데", "그냥", "정말", "진짜",
            "너무", "약간", "조금", "매우", "아주", "오늘", "어제", "내일",
            "같다", "것", "수", "좀", "때문", "완전", "진심", "약간은",
        }
        return token in stopwords

    def _lookup_token_scores(self, token: str) -> dict:
        scores = self._zero_scores()
        nrc = self.nrc_lexicon.get(token)
        knu = self.knu_lexicon.get(token)

        if nrc:
            self._accumulate(scores, nrc)
        if knu:
            self._accumulate(scores, knu)
        return scores

    def _compute_valence(self, emotions: dict) -> float:
        positive = (emotions.get('joy', 0.0) + emotions.get('trust', 0.0)) / 2.0
        negative = (
            emotions.get('sadness', 0.0)
            + emotions.get('anger', 0.0)
            + emotions.get('fear', 0.0)
        ) / 3.0
        value = positive - negative
        return round(max(-1.0, min(1.0, value)), 4)

    def _compute_arousal(self, emotions: dict) -> float:
        active = (emotions.get('anger', 0.0) + emotions.get('surprise', 0.0)) / 2.0
        calm = (emotions.get('sadness', 0.0) + emotions.get('trust', 0.0)) / 2.0
        value = active - calm
        return round(max(-1.0, min(1.0, value)), 4)

    def _primary_emotion(self, emotions: dict) -> str:
        label_map = {
            'joy': '기쁨',
            'sadness': '슬픔',
            'anger': '분노',
            'fear': '두려움',
            'trust': '신뢰',
            'surprise': '놀람',
        }
        key = max(label_map.keys(), key=lambda k: emotions.get(k, 0.0))
        if emotions.get(key, 0.0) <= 0:
            return '알수없음'
        return label_map[key]

    def _normalize_scores(self, scores: dict) -> dict:
        total = sum(max(0.0, scores.get(key, 0.0)) for key in EMOTION_KEYS)
        if total <= 0:
            return self._empty_emotions()
        return {
            key: round(max(0.0, scores.get(key, 0.0)) / total, 4)
            for key in EMOTION_KEYS
        }

    def _accumulate(self, target: dict, source: dict) -> None:
        for key in EMOTION_KEYS:
            target[key] += float(source.get(key, 0.0))

    def _has_signal(self, scores: dict) -> bool:
        return any(scores.get(key, 0.0) > 0 for key in EMOTION_KEYS)

    def _zero_scores(self) -> dict:
        return {key: 0.0 for key in EMOTION_KEYS}

    def _empty_emotions(self) -> dict:
        return self._zero_scores()

# 싱글톤 분석기 인스턴스 생성
analyzer = EmotionAnalyzer()

# ==========================================
# 3. 외부 API 호출을 받는 DRF 뷰
# ==========================================
@api_view(['POST'])
@parser_classes([JSONParser, MultiPartParser, FormParser])
@permission_classes([AllowAny])
def analyze_text_view(request):
    text = request.data.get('text', '')
    if not text:
        return Response(
            {'error': 'text 필드는 필수입니다.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        # 형태소 감정 분석 수행
        result = analyzer.analyze(text)
        return Response(result, status=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Error during analysis: {e}")
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
