//! 자기개선기(Self-Improver) — 대화가 쌓일수록 답변 품질이 올라가고, 나빠지면
//! 스스로 되돌린다 (task 9, R8).
//!
//! 이 모듈은 다섯 종류의 문제 신호를 감지하고, 누적된 신호를 원인 후보로 진단해
//! 개선 후보(Knobs)를 만들고, 홀드아웃으로 평가한 뒤 순수 승격 게이트를 통과한
//! 후보만 실사용 기본 구성으로 승격한다. 승격 후 품질이 떨어지면 자동으로
//! 되돌린다.
//!
//! 네 가지 구조적 보장이 여기서 성립한다.
//!
//! * **안전설정 불가침 (R8.6, R8.7, R12.10).** [`Knobs`]에는 안전설정 필드가
//!   없다. 프롬프트 버전·말투 파라미터·검색 가중치·few-shot 선택 네 개뿐이므로,
//!   개선 후보는 안전 변경을 *표현조차* 할 수 없다. 외부 패치의 유일한 입구인
//!   [`KnobPatch::parse`]는 알 수 없는 키(예: `allow_ax_send`)를 만나면 `Knobs`를
//!   만들지 않고 [`RejectReason::SafetyChangeRequested`]를 낸다.
//! * **원자적 스냅샷 스왑 (R8.11, R8.12, R8.13).** [`KnobStore`]는
//!   [`crate::model_config`]와 동일한 계약(검증 → 원자적 교체 → 버전 단조 증가)을
//!   따른다. 진행 중 답변은 고정된 세대로 끝까지 완료되고, 다음 답변부터 새
//!   세대를 본다.
//! * **홀드아웃 분리 (R8.8).** [`HoldoutSet::split`]은 데이터셋의 20%(최대 500건)를
//!   평가 전용으로 떼어 내며, 학습셋과 교집합이 0이 되도록 값 수준에서 분할한다.
//! * **로컬 전용 (R8.15, R12.9).** 감지·진단·평가·승격·롤백 전 과정에서 외부
//!   네트워크 송신이 0이고, `is_local_only == true`인 임베더만 쓴다. 품질 점수는
//!   외부 심판 없이 [`crate::experiment::score_reply`]로 로컬에서 계산한다.
//!   [`NetworkPort`]를 주입해 하네스가 egress == 0을 단정할 수 있게 한다.
//!
//! 저장되는 어떤 열에도 채팅 본문·생성된 답변·프롬프트·URL·절대 경로·계정
//! 식별정보가 남지 않는다. 방·처리·표본 식별은 `context::provenance_id`로 해시한
//! `*_pid`만 쓰고, `knob_generation`의 `knobs_json`은 프롬프트 *버전 라벨*과
//! provenance 해시만 담는다 (R8.16, R12.4).

use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Arc, RwLock};

use rand::rngs::StdRng;
use rand::seq::SliceRandom;
use rand::SeedableRng;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::context::provenance_id;
use crate::experiment::{score_reply, StyleTarget};
use crate::ports::{Clock, NetworkPort};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// 답변 품질 점수가 이 값 미만이면 저품질 신호 (R8.1-1).
pub const LOW_QUALITY_BELOW: u8 = 60;
/// 답변 확정 후 이 시간(초) 이내의 되묻기는 재질문 신호 (R8.1-2).
pub const REASK_WITHIN_SECS: i64 = 120;
/// 반복 질문 신호의 유사도 하한 (R8.1-3).
pub const REPEAT_SIMILARITY: f32 = 0.80;
/// 답변 확정 후 이 시간(초) 이내의 소유자 정정은 정정 신호 (R8.1-4).
pub const OWNER_CORRECTION_WITHIN_SECS: i64 = 300;
/// 주제 이탈 임계값 기본값. 설정이 없으면 이 값 (R8.1-5).
pub const DRIFT_DEFAULT_THRESHOLD: f32 = 0.50;
/// 주제 이탈 임계값 설정의 하한 (R8.1-5).
pub const DRIFT_MIN_THRESHOLD: f32 = 0.30;
/// 주제 이탈 임계값 설정의 상한 (R8.1-5).
pub const DRIFT_MAX_THRESHOLD: f32 = 0.90;

/// 반복 질문 신호가 성립하는 최소 반복 횟수 (R8.1-3).
pub const REPEAT_COUNT_MIN: u32 = 3;
/// 개선 착수 판단에 쓰는 관찰 창(밀리초): 24시간 (R8.3).
pub const OBSERVE_WINDOW_MS: i64 = 24 * 60 * 60 * 1_000;
/// 같은 방·같은 종류 신호의 오탐 표시가 이 횟수 이상이면 착수 판단에서 제외
/// (R8.4).
pub const FALSE_POSITIVE_CAP: u32 = 3;
/// 홀드아웃 최대 표본 수 (R8.8).
pub const MAX_HOLDOUT: usize = 500;
/// 평가·승격에 필요한 최소 홀드아웃 표본 수 (R8.18).
pub const MIN_HOLDOUT_FOR_EVAL: usize = 50;
/// 승격에 필요한 최소 평균 품질 상승 폭 (R8.9).
pub const MIN_QUALITY_GAIN: f32 = 5.0;
/// 허용되는 응답 지연 P95 배수: 현재 구성의 120% (R8.9).
pub const MAX_LATENCY_RATIO: f32 = 1.20;
/// 회귀 판정 하락 폭 초과 기준: 60점 이상 사례가 10점 넘게 하락 (R8.10).
pub const REGRESSION_DROP_OVER: i32 = 10;
/// 회귀 판정 대상이 되는 이전 점수 하한 (R8.10).
pub const REGRESSION_BASELINE_MIN: u8 = 60;
/// 한 문제 유형에 대한 자동 개선 시도 상한 (R8.14).
pub const ATTEMPT_CAP: u8 = 3;
/// 승격 후 감시·자동 롤백 판단에 쓰는 최근 처리 건수 (R8.12).
pub const WATCH_WINDOW: usize = 20;
/// 자동 롤백을 유발하는 평균 하락 폭 (R8.12).
pub const ROLLBACK_DROP: f32 = 5.0;
/// 보관하는 최근 구성 세대 수 (R8.16, 결정 3).
pub const KEEP_GENERATIONS: usize = 10;

// ---------------------------------------------------------------------------
// SignalKind
// ---------------------------------------------------------------------------

/// 다섯 종류의 문제 신호 (R8.1).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum SignalKind {
    /// 답변의 품질 점수가 60 미만.
    LowQuality,
    /// 답변 확정 후 120초 이내에 상대가 되물음.
    Reask,
    /// 같은 상대가 유사도 0.80 이상 질문을 24시간 이내 3회 이상 반복.
    RepeatQuestion,
    /// 답변 확정 후 300초 이내에 소유자가 삭제·정정.
    OwnerCorrection,
    /// 답변 주제와 질문 주제의 유사도가 임계값 미만.
    TopicDrift,
}

impl SignalKind {
    /// 저장·비교에 쓰는 안정 문자열.
    pub fn as_str(self) -> &'static str {
        match self {
            SignalKind::LowQuality => "low_quality",
            SignalKind::Reask => "reask",
            SignalKind::RepeatQuestion => "repeat_question",
            SignalKind::OwnerCorrection => "owner_correction",
            SignalKind::TopicDrift => "topic_drift",
        }
    }

    /// 저장 문자열을 다시 [`SignalKind`]으로 해석한다.
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "low_quality" => Some(SignalKind::LowQuality),
            "reask" => Some(SignalKind::Reask),
            "repeat_question" => Some(SignalKind::RepeatQuestion),
            "owner_correction" => Some(SignalKind::OwnerCorrection),
            "topic_drift" => Some(SignalKind::TopicDrift),
            _ => None,
        }
    }
}

/// 문제 신호 한 건 (R8.1, R8.2). 본문·답변·프롬프트·URL·경로·계정 식별정보 필드가
/// 없다.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SignalRecord {
    /// 신호 종류.
    pub kind: SignalKind,
    /// 방 provenance id.
    pub room_pid: String,
    /// 감지 시각(밀리초).
    pub at: i64,
    /// 감지 기준값 코드(예: `"score<60"`). 원문을 담지 않는다 (R8.2).
    pub threshold_code: String,
    /// 사용자가 오탐으로 표시했는지 (R8.4).
    pub dismissed: bool,
}

/// [`SelfImprover::observe`]에 넘기는 한 답변에 대한 관측값 (R8.1).
///
/// 유사도·임베딩은 로컬 임베더로 이미 계산된 뒤 스칼라로 전달되므로, 감지 판정은
/// 순수 함수로 남고 이 모듈은 외부 호출을 하지 않는다.
#[derive(Debug, Clone, PartialEq)]
pub struct ReplyObservation {
    /// 방 provenance id.
    pub room_pid: String,
    /// 관측 시각(밀리초).
    pub at: i64,
    /// 답변의 로컬 품질 점수 (0..=100).
    pub quality_score: u8,
    /// 상대가 되물은 경우, 답변 확정부터 되묻기까지의 초. 되묻지 않았으면 `None`.
    pub reask_secs: Option<i64>,
    /// 같은 상대가 24시간 이내에 유사도 0.80 이상으로 반복한 횟수.
    pub repeat_count: u32,
    /// 소유자가 정정·삭제한 경우 그때까지의 초. 없으면 `None`.
    pub owner_correction_secs: Option<i64>,
    /// 답변 주제와 질문 주제의 유사도 (0.0..=1.0).
    pub topic_similarity: f32,
}

/// 원시 설정값을 적용 가능한 주제 이탈 임계값으로 해석한다. `0.30..=0.90`은
/// 그대로, 그 밖(또는 `None`)은 [`DRIFT_DEFAULT_THRESHOLD`] (R8.1-5).
pub fn resolve_drift_threshold(raw: Option<f32>) -> f32 {
    match raw {
        Some(v) if (DRIFT_MIN_THRESHOLD..=DRIFT_MAX_THRESHOLD).contains(&v) => v,
        _ => DRIFT_DEFAULT_THRESHOLD,
    }
}

/// 한 관측값에서 감지되는 신호 종류 집합을 계산하는 순수 함수 (R8.1).
///
/// `drift_threshold`는 [`resolve_drift_threshold`]로 이미 해석된 값이다.
pub fn detect_signals(obs: &ReplyObservation, drift_threshold: f32) -> Vec<(SignalKind, String)> {
    let mut out = Vec::new();
    if obs.quality_score < LOW_QUALITY_BELOW {
        out.push((SignalKind::LowQuality, format!("score<{LOW_QUALITY_BELOW}")));
    }
    if let Some(secs) = obs.reask_secs {
        if (0..=REASK_WITHIN_SECS).contains(&secs) {
            out.push((SignalKind::Reask, format!("reask<={REASK_WITHIN_SECS}s")));
        }
    }
    if obs.repeat_count >= REPEAT_COUNT_MIN {
        out.push((
            SignalKind::RepeatQuestion,
            format!("repeat>={REPEAT_COUNT_MIN}@{REPEAT_SIMILARITY:.2}"),
        ));
    }
    if let Some(secs) = obs.owner_correction_secs {
        if (0..=OWNER_CORRECTION_WITHIN_SECS).contains(&secs) {
            out.push((
                SignalKind::OwnerCorrection,
                format!("owner_correction<={OWNER_CORRECTION_WITHIN_SECS}s"),
            ));
        }
    }
    if obs.topic_similarity < drift_threshold {
        out.push((SignalKind::TopicDrift, format!("drift<{drift_threshold:.2}")));
    }
    out
}

// ---------------------------------------------------------------------------
// Knobs (개선 후보가 바꿀 수 있는 전부 — 안전설정 필드 없음)
// ---------------------------------------------------------------------------

/// 말투프로필 파라미터.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StyleParams {
    /// 존댓말 편향 가중치.
    pub honorific_bias: f32,
    /// 격식 편향 가중치.
    pub formality_bias: f32,
    /// 목표 답변 길이(문자 수).
    pub target_len: u32,
}

/// 검색 가중치.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetrievalWeights {
    /// 최근성 가중치.
    pub recency: f32,
    /// 유사도 가중치.
    pub similarity: f32,
    /// 수신자 일치 가중치.
    pub recipient: f32,
}

/// few-shot 예시 선택. 원문이 아니라 provenance id만 담는다 (R8.16, R12.4).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FewShotSelection {
    /// 선택된 예시의 provenance id 목록.
    pub example_pids: Vec<String>,
    /// 예시 개수(= `example_pids.len()`).
    pub count: u32,
}

/// 개선이 바꿀 수 있는 전부 (R8.6). 안전설정 필드가 없으므로 안전 변경을
/// 표현할 수 없다 (R8.7, R12.10).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Knobs {
    /// 프롬프트 버전 라벨(예: `v2`). 원문이 아닌 버전 식별자다.
    pub prompt_version: String,
    /// 말투프로필 파라미터.
    pub style_params: StyleParams,
    /// 검색 가중치.
    pub retrieval_weights: RetrievalWeights,
    /// few-shot 예시 선택.
    pub few_shot: FewShotSelection,
}

/// 구성의 안정 다이제스트. 롤백 이력·재승격 거부에 쓴다 (R8.19).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct KnobsDigest(pub String);

/// 개선이 바꿀 수 있는 네 가지 항목의 키 (R8.6).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum KnobKey {
    /// 프롬프트 버전.
    PromptVersion,
    /// 말투프로필 파라미터.
    StyleParams,
    /// 검색 가중치.
    RetrievalWeights,
    /// few-shot 예시 선택.
    FewShot,
}

impl KnobKey {
    /// 저장·표시에 쓰는 안정 문자열.
    pub fn as_str(self) -> &'static str {
        match self {
            KnobKey::PromptVersion => "prompt_version",
            KnobKey::StyleParams => "style_params",
            KnobKey::RetrievalWeights => "retrieval_weights",
            KnobKey::FewShot => "few_shot",
        }
    }
}

/// [`KnobPatch::parse`]가 허용하는 최상위 키. 이 넷 이외는 안전설정으로 간주해
/// 거부한다 (R8.7).
const ALLOWED_KEYS: [&str; 4] = [
    "prompt_version",
    "style_params",
    "retrieval_weights",
    "few_shot",
];

impl Knobs {
    /// 구성의 안정 다이제스트. 필드 순서가 고정된 JSON을 provenance 해시한다.
    pub fn digest(&self) -> KnobsDigest {
        let json = serde_json::to_string(self).unwrap_or_default();
        KnobsDigest(provenance_id(&json))
    }

    /// 원문·경로·URL을 담지 않는 직렬화 문자열. `knob_generation.knobs_json`에
    /// 저장된다 (R8.16, R12.4).
    pub fn to_json(&self) -> String {
        serde_json::to_string(self).unwrap_or_default()
    }

    /// 저장된 `knobs_json`을 다시 [`Knobs`]로 해석한다.
    pub fn from_json(s: &str) -> Result<Knobs, ImproveError> {
        serde_json::from_str(s).map_err(|e| ImproveError::InvalidKnobs(e.to_string()))
    }

    /// 두 구성 사이에 바뀐 키 집합 (R8.11의 "바뀐 항목", 항상 네 항목의 부분집합,
    /// R12.12).
    pub fn changed_keys(&self, other: &Knobs) -> Vec<KnobKey> {
        let mut keys = Vec::new();
        if self.prompt_version != other.prompt_version {
            keys.push(KnobKey::PromptVersion);
        }
        if self.style_params != other.style_params {
            keys.push(KnobKey::StyleParams);
        }
        if self.retrieval_weights != other.retrieval_weights {
            keys.push(KnobKey::RetrievalWeights);
        }
        if self.few_shot != other.few_shot {
            keys.push(KnobKey::FewShot);
        }
        keys
    }
}

/// 구성이 자기 자신과 정합적인지 검증한다. 원자적 교체 전에 호출된다.
fn validate_knobs(k: &Knobs) -> Result<(), ImproveError> {
    if k.prompt_version.trim().is_empty() {
        return Err(ImproveError::InvalidKnobs(
            "프롬프트 버전이 비어 있어요".to_string(),
        ));
    }
    if k.style_params.target_len == 0 {
        return Err(ImproveError::InvalidKnobs(
            "목표 답변 길이는 1 이상이어야 해요".to_string(),
        ));
    }
    for w in [
        k.retrieval_weights.recency,
        k.retrieval_weights.similarity,
        k.retrieval_weights.recipient,
        k.style_params.honorific_bias,
        k.style_params.formality_bias,
    ] {
        if !w.is_finite() {
            return Err(ImproveError::InvalidKnobs(
                "가중치 값이 올바르지 않아요".to_string(),
            ));
        }
    }
    if k.few_shot.count as usize != k.few_shot.example_pids.len() {
        return Err(ImproveError::InvalidKnobs(
            "few-shot 개수와 예시 수가 일치하지 않아요".to_string(),
        ));
    }
    Ok(())
}

/// 외부에서 온 패치의 유일한 입구.
///
/// 알 수 없는 키(안전설정 등)를 만나면 `Knobs`를 만들지 않고
/// [`RejectReason::SafetyChangeRequested`]를 낸다 (R8.7, R12.10). 그 밖의
/// 구조적 오류는 [`RejectReason::MalformedPatch`]로 거부한다.
pub struct KnobPatch;

impl KnobPatch {
    /// 원시 JSON을 [`Knobs`]로 해석한다.
    pub fn parse(raw: &serde_json::Value) -> Result<Knobs, RejectReason> {
        let obj = raw
            .as_object()
            .ok_or_else(|| RejectReason::MalformedPatch("객체가 아니에요".to_string()))?;
        // 안전설정을 포함한 임의 키는 여기서 거부된다 — Knobs가 만들어지지 않는다.
        for key in obj.keys() {
            if !ALLOWED_KEYS.contains(&key.as_str()) {
                return Err(RejectReason::SafetyChangeRequested);
            }
        }
        match serde_json::from_value::<Knobs>(raw.clone()) {
            Ok(knobs) => validate_knobs(&knobs)
                .map(|_| knobs)
                .map_err(|e| RejectReason::MalformedPatch(e.to_string())),
            Err(e) => {
                // 중첩된 안전설정 키(예: style_params 안의 임의 키)도 거부한다.
                if e.to_string().contains("unknown field") {
                    Err(RejectReason::SafetyChangeRequested)
                } else {
                    Err(RejectReason::MalformedPatch(e.to_string()))
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// KnobStore (model_config 원자적 스냅샷 스왑 계약 위에)
// ---------------------------------------------------------------------------

/// 구성 세대 번호. `model_config` 버전과 같은 단조 증가 값.
pub type KnobGeneration = u64;

/// [`KnobStore::generations`]가 돌려주는 보관된 세대.
#[derive(Debug, Clone, PartialEq)]
pub struct StoredGeneration {
    /// 세대 번호.
    pub generation: KnobGeneration,
    /// 해당 세대의 구성.
    pub knobs: Knobs,
    /// 구성 다이제스트.
    pub digest: KnobsDigest,
}

/// 구성 저장소 (R8.11, R8.12, R8.13).
///
/// [`crate::model_config::ModelConfigStore`]와 동일한 계약을 따른다: 검증 →
/// 원자적 교체 → 버전 단조 증가. 진행 중 답변은 [`KnobStore::current`]가 돌려준
/// 소유 스냅샷 위에서 완료되고, 다음 답변부터 새 세대를 본다.
pub trait KnobStore {
    /// 현재 세대와 구성을 소유 스냅샷으로 돌려준다. 반환값은 소유값이라 이후
    /// [`KnobStore::apply`]가 이를 바꾸지 못한다 (R8.12).
    fn current(&self) -> (KnobGeneration, Knobs);

    /// `next`를 검증한 뒤 원자적으로 새 스냅샷으로 교체하고 다음 세대 번호를
    /// 돌려준다. 검증 실패 시 저장 구성은 그대로 (R8.11).
    fn apply(&self, next: Knobs) -> Result<KnobGeneration, ImproveError>;

    /// 최근 세대(최대 `limit`)를 최신순으로 돌려준다 (R8.16).
    fn generations(&self, limit: usize) -> Result<Vec<StoredGeneration>, ImproveError>;
}

/// 메모리 기반 [`KnobStore`]. 현재 스냅샷은 `RwLock<Arc<..>>` 뒤에 있어 교체가
/// 동시 [`KnobStore::current`] 호출과 원자적이다. 최근 [`KEEP_GENERATIONS`]개
/// 세대를 링으로 보관한다.
#[derive(Debug)]
pub struct InMemoryKnobStore {
    inner: RwLock<Arc<(KnobGeneration, Knobs)>>,
    history: RwLock<Vec<StoredGeneration>>,
}

impl InMemoryKnobStore {
    /// 검증된 초기 구성으로 저장소를 만든다. 초기 스냅샷은 세대 `1`이다.
    pub fn new(initial: Knobs) -> Result<Self, ImproveError> {
        validate_knobs(&initial)?;
        let digest = initial.digest();
        let seeded = StoredGeneration {
            generation: 1,
            knobs: initial.clone(),
            digest,
        };
        Ok(Self {
            inner: RwLock::new(Arc::new((1, initial))),
            history: RwLock::new(vec![seeded]),
        })
    }
}

impl KnobStore for InMemoryKnobStore {
    fn current(&self) -> (KnobGeneration, Knobs) {
        let guard = self.inner.read().expect("knob store lock poisoned on read");
        (guard.0, guard.1.clone())
    }

    fn apply(&self, next: Knobs) -> Result<KnobGeneration, ImproveError> {
        // 저장 스냅샷을 건드리기 전에 검증한다 (R8.11).
        validate_knobs(&next)?;
        let mut guard = self
            .inner
            .write()
            .expect("knob store lock poisoned on write");
        let next_gen = guard.0.saturating_add(1);
        let digest = next.digest();
        *guard = Arc::new((next_gen, next.clone()));
        drop(guard);

        let mut history = self
            .history
            .write()
            .expect("knob history lock poisoned on write");
        history.push(StoredGeneration {
            generation: next_gen,
            knobs: next,
            digest,
        });
        // 최근 KEEP_GENERATIONS개만 유지한다.
        let len = history.len();
        if len > KEEP_GENERATIONS {
            history.drain(0..len - KEEP_GENERATIONS);
        }
        Ok(next_gen)
    }

    fn generations(&self, limit: usize) -> Result<Vec<StoredGeneration>, ImproveError> {
        let history = self
            .history
            .read()
            .expect("knob history lock poisoned on read");
        Ok(history.iter().rev().take(limit).cloned().collect())
    }
}

// ---------------------------------------------------------------------------
// Holdout / 평가 / 승격 게이트
// ---------------------------------------------------------------------------

/// 평가에 쓰는 질문-답변 표본 한 건. 질문 원문은 메모리에서만 다루고 저장하지
/// 않는다 (R8.16).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct QaSample {
    /// `qa_pair` 행 id.
    pub qa_pair_id: i64,
    /// 표본 provenance id.
    pub sample_pid: String,
    /// 평가용 질문 텍스트(메모리 전용).
    pub question: String,
}

/// 평가 전용으로 분리한 홀드아웃 (R8.8).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct HoldoutSet {
    /// 홀드아웃 표본.
    pub items: Vec<QaSample>,
}

impl HoldoutSet {
    /// 전체 표본 수에 대한 홀드아웃 크기: 20%와 500 중 작은 값 (R8.8).
    pub fn holdout_len_for(total: usize) -> usize {
        (total / 5).min(MAX_HOLDOUT)
    }

    /// 전체 표본을 `(학습셋, 홀드아웃)`으로 분할한다. 시드 기반 결정적 셔플로
    /// 뽑되 값 수준 분할이므로 교집합이 0이다 (R8.8).
    pub fn split(all: Vec<QaSample>, seed: u64) -> (Vec<QaSample>, HoldoutSet) {
        let holdout_len = Self::holdout_len_for(all.len());
        let mut indices: Vec<usize> = (0..all.len()).collect();
        let mut rng = StdRng::seed_from_u64(seed);
        indices.shuffle(&mut rng);
        let holdout_idx: BTreeSet<usize> = indices.into_iter().take(holdout_len).collect();

        let mut train = Vec::new();
        let mut holdout = Vec::new();
        for (i, sample) in all.into_iter().enumerate() {
            if holdout_idx.contains(&i) {
                holdout.push(sample);
            } else {
                train.push(sample);
            }
        }
        (train, HoldoutSet { items: holdout })
    }

    /// 홀드아웃 표본 수.
    pub fn len(&self) -> usize {
        self.items.len()
    }

    /// 홀드아웃이 비었는지.
    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }
}

/// 회귀 한 건: 현재 구성에서 60점 이상이던 사례가 후보에서 10점 넘게 하락 (R8.10).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Regression {
    /// 회귀가 난 표본 provenance id.
    pub sample_pid: String,
    /// 현재 구성 점수.
    pub before: u8,
    /// 후보 구성 점수.
    pub after: u8,
}

/// 한 구성에 대한 홀드아웃 평가 요약 (R8.9).
#[derive(Debug, Clone, PartialEq)]
pub struct EvalSummary {
    /// 평균 품질 점수.
    pub mean_quality: f32,
    /// 응답 지연 P95(밀리초).
    pub p95_latency_ms: u64,
    /// 평가한 표본 수.
    pub evaluated: usize,
    /// 표본별 품질 점수(sample_pid → score). 회귀 계산에 쓴다.
    pub scores: BTreeMap<String, u8>,
}

/// 두 평가 요약을 비교해 회귀 목록을 계산한다 (R8.10).
///
/// 현재 구성에서 60점 이상이던 사례가 후보에서 10점 넘게 떨어진 경우만 회귀다.
pub fn compute_regressions(current: &EvalSummary, candidate: &EvalSummary) -> Vec<Regression> {
    let mut out = Vec::new();
    for (pid, &before) in &current.scores {
        if before < REGRESSION_BASELINE_MIN {
            continue;
        }
        if let Some(&after) = candidate.scores.get(pid) {
            if (before as i32 - after as i32) > REGRESSION_DROP_OVER {
                out.push(Regression {
                    sample_pid: pid.clone(),
                    before,
                    after,
                });
            }
        }
    }
    out
}

/// 승격이 거부된 이유 (R8.7, R8.9, R8.10, R8.14, R8.18, R8.19).
#[derive(Debug, Clone, PartialEq)]
pub enum RejectReason {
    /// 평균 품질 상승 폭이 5점 미만 (R8.19).
    GainTooSmall {
        /// 실제 상승 폭.
        delta: f32,
    },
    /// 응답 지연 P95가 현재 120%를 초과 (R8.19).
    LatencyRegression {
        /// 현재 대비 배수.
        ratio: f32,
    },
    /// 회귀 사례가 있음 (R8.10).
    Regressions(Vec<Regression>),
    /// 이전에 롤백된 구성 (R8.19/R8.20).
    PreviouslyRolledBack,
    /// 안전설정 변경을 요청함 (R8.7).
    SafetyChangeRequested,
    /// 자동 개선 시도 3회에 도달 (R8.14).
    AttemptCapReached {
        /// 상한.
        cap: u8,
    },
    /// 홀드아웃 표본이 50건 미만 (R8.18).
    InsufficientHoldout {
        /// 현재 표본 수.
        have: usize,
        /// 필요한 표본 수.
        need: usize,
    },
    /// 패치가 구조적으로 올바르지 않음(안전설정 외 오류).
    MalformedPatch(String),
}

impl RejectReason {
    /// 안정 사유 코드(이력·저널 저장용, 원문 없음).
    pub fn code(&self) -> &'static str {
        match self {
            RejectReason::GainTooSmall { .. } => "gain_too_small",
            RejectReason::LatencyRegression { .. } => "latency_regression",
            RejectReason::Regressions(_) => "regressions",
            RejectReason::PreviouslyRolledBack => "previously_rolled_back",
            RejectReason::SafetyChangeRequested => "safety_change_requested",
            RejectReason::AttemptCapReached { .. } => "attempt_cap_reached",
            RejectReason::InsufficientHoldout { .. } => "insufficient_holdout",
            RejectReason::MalformedPatch(_) => "malformed_patch",
        }
    }
}

/// 순수 승격 게이트 (R8.9, R8.14, R8.18, R8.19, R8.20, R12.11).
///
/// 통과(=`Ok(())`)는 다음 여섯 조건이 모두 참인 것과 동치다:
/// 홀드아웃 ≥ 50 ∧ 평균 상승 ≥ 5점 ∧ P95 ≤ 현재 120% ∧ 회귀 0 ∧ 롤백 이력에
/// 없음 ∧ 시도 3회 미도달. 하나라도 거짓이면 위반 사유를 낸다.
pub fn promotion_gate(
    current: &EvalSummary,
    candidate: &EvalSummary,
    holdout_len: usize,
    rolled_back: &BTreeSet<KnobsDigest>,
    digest: &KnobsDigest,
    attempts: u8,
) -> Result<(), RejectReason> {
    // 이전에 되돌린 구성은 다시 올라와도 거부 (R8.20).
    if rolled_back.contains(digest) {
        return Err(RejectReason::PreviouslyRolledBack);
    }
    // 시도 상한 도달 (R8.14).
    if attempts >= ATTEMPT_CAP {
        return Err(RejectReason::AttemptCapReached { cap: ATTEMPT_CAP });
    }
    // 홀드아웃 부족 (R8.18).
    if holdout_len < MIN_HOLDOUT_FOR_EVAL {
        return Err(RejectReason::InsufficientHoldout {
            have: holdout_len,
            need: MIN_HOLDOUT_FOR_EVAL,
        });
    }
    // 회귀 0 (R8.10).
    let regressions = compute_regressions(current, candidate);
    if !regressions.is_empty() {
        return Err(RejectReason::Regressions(regressions));
    }
    // 평균 상승 ≥ 5점 (R8.9, R8.19).
    let delta = candidate.mean_quality - current.mean_quality;
    if delta < MIN_QUALITY_GAIN {
        return Err(RejectReason::GainTooSmall { delta });
    }
    // P95 ≤ 현재 120% (R8.9, R8.19).
    let ratio = if current.p95_latency_ms == 0 {
        if candidate.p95_latency_ms == 0 {
            1.0
        } else {
            f32::INFINITY
        }
    } else {
        candidate.p95_latency_ms as f32 / current.p95_latency_ms as f32
    };
    if ratio > MAX_LATENCY_RATIO {
        return Err(RejectReason::LatencyRegression { ratio });
    }
    Ok(())
}

/// 승격 결과 (R8.11, R8.18).
#[derive(Debug, Clone, PartialEq)]
pub enum PromotionOutcome {
    /// 승격됨.
    Promoted {
        /// 새 세대 번호.
        generation: KnobGeneration,
        /// 바뀐 키.
        changed_keys: Vec<KnobKey>,
        /// 승격 시각.
        at: i64,
        /// 홀드아웃 평균 품질 변화 폭.
        delta: f32,
    },
    /// 게이트에서 거부됨.
    Rejected(RejectReason),
    /// 홀드아웃 표본 부족으로 평가·승격을 하지 않음 (R8.18).
    Insufficient {
        /// 현재 표본 수.
        have: usize,
        /// 필요한 표본 수.
        need: usize,
    },
}

/// 자동/수동 롤백 결과 (R8.12, R8.13).
#[derive(Debug, Clone, PartialEq)]
pub struct RollbackOutcome {
    /// 되돌린 뒤 활성 구성의 세대.
    pub generation: KnobGeneration,
    /// 승격 직전 20건 평균.
    pub before_mean: f32,
    /// 승격 후 20건 평균.
    pub after_mean: f32,
}

// ---------------------------------------------------------------------------
// 진단
// ---------------------------------------------------------------------------

/// 누적 신호에서 도출한 원인 후보 (R8.5). 항상 비어 있지 않고 네 항목의
/// 부분집합이다.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Diagnosis {
    /// 원인 후보 키(중복 없이 정렬).
    pub causes: Vec<KnobKey>,
}

/// 한 신호 종류를 원인 후보로 매핑한다.
fn causes_for(kind: SignalKind) -> &'static [KnobKey] {
    match kind {
        SignalKind::LowQuality => &[KnobKey::StyleParams, KnobKey::PromptVersion],
        SignalKind::Reask => &[KnobKey::PromptVersion, KnobKey::FewShot],
        SignalKind::RepeatQuestion => &[KnobKey::RetrievalWeights, KnobKey::FewShot],
        SignalKind::OwnerCorrection => &[KnobKey::PromptVersion, KnobKey::StyleParams],
        SignalKind::TopicDrift => &[KnobKey::RetrievalWeights],
    }
}

// ---------------------------------------------------------------------------
// 오류
// ---------------------------------------------------------------------------

/// 자기개선 저장소/구성 오류.
#[derive(Debug, Error)]
pub enum ImproveError {
    /// 구성이 올바르지 않음.
    #[error("개선 구성이 올바르지 않아요: {0}")]
    InvalidKnobs(String),
    /// 요청한 세대를 찾지 못함.
    #[error("구성 세대를 찾지 못했어요: {0}")]
    GenerationNotFound(KnobGeneration),
    /// 저장소 실패.
    #[error("자기개선 저장소 오류: {0}")]
    Backend(String),
}

fn db_err(e: rusqlite::Error) -> ImproveError {
    ImproveError::Backend(e.to_string())
}

// ---------------------------------------------------------------------------
// ImproveStore
// ---------------------------------------------------------------------------

/// 한 이력 항목(감지·진단·승격·거부·롤백) (R8.16).
#[derive(Debug, Clone, PartialEq)]
pub struct HistoryEntry {
    /// 시각.
    pub at: i64,
    /// 문제 유형(신호 종류 문자열).
    pub problem_kind: String,
    /// 바뀐 항목(쉼표 구분 키).
    pub changed_keys: String,
    /// 판정 결과 코드.
    pub verdict: String,
    /// 관련 세대(있으면).
    pub generation: Option<KnobGeneration>,
}

/// 보관된 구성 세대 한 건(sqlite `knob_generation`) (R8.16, R8.17).
#[derive(Debug, Clone, PartialEq)]
pub struct GenerationRecord {
    /// 세대 번호.
    pub generation: KnobGeneration,
    /// 해당 세대 구성.
    pub knobs: Knobs,
    /// 구성 다이제스트.
    pub digest: KnobsDigest,
    /// 승격 시각.
    pub promoted_at: i64,
    /// 승격 시점의 홀드아웃 평균(0..=100) (R8.17).
    pub holdout_mean: u8,
}

/// 자기개선 상태의 지속 경계 (R8.4, R8.14, R8.16, R8.19).
pub trait ImproveStore {
    /// 문제 신호 한 건을 기록한다.
    fn record_signal(&self, s: &SignalRecord) -> Result<(), ImproveError>;

    /// `room_pid`에서 `since` 이후의 오탐 아닌 신호를 돌려준다.
    fn active_signals_since(
        &self,
        room_pid: &str,
        since: i64,
    ) -> Result<Vec<SignalRecord>, ImproveError>;

    /// 같은 방·종류 신호를 오탐으로 표시하고 오탐 횟수를 1 늘려 새 값을 돌려준다
    /// (R8.4).
    fn dismiss(&self, room_pid: &str, kind: SignalKind) -> Result<u32, ImproveError>;

    /// 같은 방·종류의 오탐 표시 횟수 (R8.4).
    fn false_positive_count(&self, room_pid: &str, kind: SignalKind)
        -> Result<u32, ImproveError>;

    /// 같은 문제 유형(방·종류)의 개선 시도 횟수 (R8.14).
    fn attempts(&self, room_pid: &str, kind: SignalKind) -> Result<u8, ImproveError>;

    /// 같은 문제 유형의 시도 횟수를 1 늘려(상한 3) 새 값을 돌려준다 (R8.14).
    fn bump_attempt(&self, room_pid: &str, kind: SignalKind) -> Result<u8, ImproveError>;

    /// 구성 세대를 기록한다(최근 10개만 유지) (R8.16).
    fn record_generation(&self, rec: &GenerationRecord) -> Result<(), ImproveError>;

    /// 세대 하나를 조회한다.
    fn generation(&self, generation: KnobGeneration)
        -> Result<Option<GenerationRecord>, ImproveError>;

    /// 롤백된 구성 다이제스트 집합 (R8.19).
    fn rolled_back_digests(&self) -> Result<BTreeSet<KnobsDigest>, ImproveError>;

    /// 롤백된 구성 다이제스트를 기록한다 (R8.19).
    fn record_rolled_back(&self, digest: &KnobsDigest, at: i64) -> Result<(), ImproveError>;

    /// 이력 항목을 남긴다 (R8.16).
    fn record_history(&self, entry: &HistoryEntry) -> Result<(), ImproveError>;

    /// 최근 이력을 최신순으로 돌려준다 (R8.16).
    fn history(&self, limit: usize) -> Result<Vec<HistoryEntry>, ImproveError>;

    /// 홀드아웃 멤버를 교체 저장한다 (R8.8).
    fn set_holdout(&self, qa_pair_ids: &[i64]) -> Result<(), ImproveError>;

    /// 홀드아웃 멤버 id를 돌려준다.
    fn holdout_ids(&self) -> Result<Vec<i64>, ImproveError>;
}

/// SQLite 기반 [`ImproveStore`]. `problem_signal`·`signal_false_positive`·
/// `knob_generation`·`rolled_back_knobs`·`improve_history`·`improve_attempt`·
/// `holdout_member` 테이블 위에서 동작한다.
pub struct SqliteImproveStore {
    conn: Connection,
}

impl SqliteImproveStore {
    /// 연결을 감싸고 스키마를 보장한다.
    pub fn new(conn: Connection) -> Result<Self, ImproveError> {
        ensure_improve_schema(&conn)?;
        Ok(Self { conn })
    }

    /// `path`에 저장소를 열거나 만든다.
    pub fn open(path: &std::path::Path) -> Result<Self, ImproveError> {
        let conn = Connection::open(path).map_err(db_err)?;
        Self::new(conn)
    }

    /// 메모리 저장소를 연다. 주로 테스트용.
    pub fn open_in_memory() -> Result<Self, ImproveError> {
        let conn = Connection::open_in_memory().map_err(db_err)?;
        Self::new(conn)
    }
}

/// 자기개선 신규 테이블을 없으면 만든다 (R8 스키마).
///
/// 어떤 열에도 채팅 본문·답변·프롬프트·URL·절대 경로·계정 식별정보가 없다:
/// 방·표본 식별은 `*_pid`(provenance 해시)만, `knobs_json`은 버전 라벨과 해시만
/// 담는다 (R8.16, R12.4).
pub fn ensure_improve_schema(conn: &Connection) -> Result<(), ImproveError> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS problem_signal(
            id INTEGER PRIMARY KEY,
            room_pid TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN
                ('low_quality','reask','repeat_question','owner_correction','topic_drift')),
            threshold_code TEXT NOT NULL,
            at INTEGER NOT NULL,
            dismissed INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_problem_signal_room
            ON problem_signal(room_pid, kind, at DESC);
        CREATE TABLE IF NOT EXISTS signal_false_positive(
            room_pid TEXT NOT NULL,
            kind TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(room_pid, kind)
        );
        CREATE TABLE IF NOT EXISTS knob_generation(
            generation INTEGER PRIMARY KEY,
            knobs_json TEXT NOT NULL,
            knobs_digest TEXT NOT NULL,
            promoted_at INTEGER NOT NULL,
            holdout_mean INTEGER NOT NULL CHECK(holdout_mean BETWEEN 0 AND 100)
        );
        CREATE TABLE IF NOT EXISTS rolled_back_knobs(
            knobs_digest TEXT PRIMARY KEY,
            at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS improve_history(
            id INTEGER PRIMARY KEY,
            at INTEGER NOT NULL,
            problem_kind TEXT NOT NULL,
            changed_keys TEXT NOT NULL,
            verdict TEXT NOT NULL,
            generation INTEGER
        );
        CREATE TABLE IF NOT EXISTS improve_attempt(
            room_pid TEXT NOT NULL,
            kind TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts <= 3),
            PRIMARY KEY(room_pid, kind)
        );
        CREATE TABLE IF NOT EXISTS holdout_member(
            qa_pair_id INTEGER PRIMARY KEY
        );",
    )
    .map_err(db_err)
}

impl ImproveStore for SqliteImproveStore {
    fn record_signal(&self, s: &SignalRecord) -> Result<(), ImproveError> {
        self.conn
            .execute(
                "INSERT INTO problem_signal(room_pid, kind, threshold_code, at, dismissed)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params![
                    s.room_pid,
                    s.kind.as_str(),
                    s.threshold_code,
                    s.at,
                    s.dismissed as i64,
                ],
            )
            .map_err(db_err)?;
        Ok(())
    }

    fn active_signals_since(
        &self,
        room_pid: &str,
        since: i64,
    ) -> Result<Vec<SignalRecord>, ImproveError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT kind, threshold_code, at FROM problem_signal
                 WHERE room_pid = ?1 AND at >= ?2 AND dismissed = 0
                 ORDER BY at DESC",
            )
            .map_err(db_err)?;
        let rows = stmt
            .query_map(params![room_pid, since], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, i64>(2)?,
                ))
            })
            .map_err(db_err)?;
        let mut out = Vec::new();
        for row in rows {
            let (kind, threshold_code, at) = row.map_err(db_err)?;
            if let Some(kind) = SignalKind::parse(&kind) {
                out.push(SignalRecord {
                    kind,
                    room_pid: room_pid.to_string(),
                    at,
                    threshold_code,
                    dismissed: false,
                });
            }
        }
        Ok(out)
    }

    fn dismiss(&self, room_pid: &str, kind: SignalKind) -> Result<u32, ImproveError> {
        self.conn
            .execute(
                "UPDATE problem_signal SET dismissed = 1 WHERE room_pid = ?1 AND kind = ?2",
                params![room_pid, kind.as_str()],
            )
            .map_err(db_err)?;
        self.conn
            .execute(
                "INSERT INTO signal_false_positive(room_pid, kind, count)
                 VALUES (?1, ?2, 1)
                 ON CONFLICT(room_pid, kind) DO UPDATE SET count = count + 1",
                params![room_pid, kind.as_str()],
            )
            .map_err(db_err)?;
        self.false_positive_count(room_pid, kind)
    }

    fn false_positive_count(
        &self,
        room_pid: &str,
        kind: SignalKind,
    ) -> Result<u32, ImproveError> {
        let count: Option<i64> = self
            .conn
            .query_row(
                "SELECT count FROM signal_false_positive WHERE room_pid = ?1 AND kind = ?2",
                params![room_pid, kind.as_str()],
                |row| row.get(0),
            )
            .optional()
            .map_err(db_err)?;
        Ok(count.unwrap_or(0).max(0) as u32)
    }

    fn attempts(&self, room_pid: &str, kind: SignalKind) -> Result<u8, ImproveError> {
        let count: Option<i64> = self
            .conn
            .query_row(
                "SELECT attempts FROM improve_attempt WHERE room_pid = ?1 AND kind = ?2",
                params![room_pid, kind.as_str()],
                |row| row.get(0),
            )
            .optional()
            .map_err(db_err)?;
        Ok(count.unwrap_or(0).clamp(0, ATTEMPT_CAP as i64) as u8)
    }

    fn bump_attempt(&self, room_pid: &str, kind: SignalKind) -> Result<u8, ImproveError> {
        // 상한 3을 넘지 않도록 MIN으로 올린다 (CHECK 제약 보존).
        self.conn
            .execute(
                "INSERT INTO improve_attempt(room_pid, kind, attempts)
                 VALUES (?1, ?2, 1)
                 ON CONFLICT(room_pid, kind) DO UPDATE SET
                    attempts = MIN(attempts + 1, 3)",
                params![room_pid, kind.as_str()],
            )
            .map_err(db_err)?;
        self.attempts(room_pid, kind)
    }

    fn record_generation(&self, rec: &GenerationRecord) -> Result<(), ImproveError> {
        self.conn
            .execute(
                "INSERT INTO knob_generation(
                    generation, knobs_json, knobs_digest, promoted_at, holdout_mean
                 ) VALUES (?1, ?2, ?3, ?4, ?5)
                 ON CONFLICT(generation) DO UPDATE SET
                    knobs_json = excluded.knobs_json,
                    knobs_digest = excluded.knobs_digest,
                    promoted_at = excluded.promoted_at,
                    holdout_mean = excluded.holdout_mean",
                params![
                    rec.generation as i64,
                    rec.knobs.to_json(),
                    rec.digest.0,
                    rec.promoted_at,
                    rec.holdout_mean as i64,
                ],
            )
            .map_err(db_err)?;
        // 최근 KEEP_GENERATIONS개만 유지 (R8.16).
        self.conn
            .execute(
                "DELETE FROM knob_generation WHERE generation NOT IN (
                    SELECT generation FROM knob_generation ORDER BY generation DESC LIMIT ?1
                 )",
                params![KEEP_GENERATIONS as i64],
            )
            .map_err(db_err)?;
        Ok(())
    }

    fn generation(
        &self,
        generation: KnobGeneration,
    ) -> Result<Option<GenerationRecord>, ImproveError> {
        let row = self
            .conn
            .query_row(
                "SELECT knobs_json, knobs_digest, promoted_at, holdout_mean
                 FROM knob_generation WHERE generation = ?1",
                params![generation as i64],
                |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, i64>(2)?,
                        row.get::<_, i64>(3)?,
                    ))
                },
            )
            .optional()
            .map_err(db_err)?;
        match row {
            None => Ok(None),
            Some((json, digest, promoted_at, holdout_mean)) => Ok(Some(GenerationRecord {
                generation,
                knobs: Knobs::from_json(&json)?,
                digest: KnobsDigest(digest),
                promoted_at,
                holdout_mean: holdout_mean.clamp(0, 100) as u8,
            })),
        }
    }

    fn rolled_back_digests(&self) -> Result<BTreeSet<KnobsDigest>, ImproveError> {
        let mut stmt = self
            .conn
            .prepare("SELECT knobs_digest FROM rolled_back_knobs")
            .map_err(db_err)?;
        let rows = stmt
            .query_map([], |row| row.get::<_, String>(0))
            .map_err(db_err)?;
        let mut out = BTreeSet::new();
        for row in rows {
            out.insert(KnobsDigest(row.map_err(db_err)?));
        }
        Ok(out)
    }

    fn record_rolled_back(&self, digest: &KnobsDigest, at: i64) -> Result<(), ImproveError> {
        self.conn
            .execute(
                "INSERT OR IGNORE INTO rolled_back_knobs(knobs_digest, at) VALUES (?1, ?2)",
                params![digest.0, at],
            )
            .map_err(db_err)?;
        Ok(())
    }

    fn record_history(&self, entry: &HistoryEntry) -> Result<(), ImproveError> {
        self.conn
            .execute(
                "INSERT INTO improve_history(at, problem_kind, changed_keys, verdict, generation)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params![
                    entry.at,
                    entry.problem_kind,
                    entry.changed_keys,
                    entry.verdict,
                    entry.generation.map(|g| g as i64),
                ],
            )
            .map_err(db_err)?;
        Ok(())
    }

    fn history(&self, limit: usize) -> Result<Vec<HistoryEntry>, ImproveError> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT at, problem_kind, changed_keys, verdict, generation
                 FROM improve_history ORDER BY at DESC, id DESC LIMIT ?1",
            )
            .map_err(db_err)?;
        let rows = stmt
            .query_map(params![limit as i64], |row| {
                Ok(HistoryEntry {
                    at: row.get(0)?,
                    problem_kind: row.get(1)?,
                    changed_keys: row.get(2)?,
                    verdict: row.get(3)?,
                    generation: row.get::<_, Option<i64>>(4)?.map(|g| g as u64),
                })
            })
            .map_err(db_err)?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row.map_err(db_err)?);
        }
        Ok(out)
    }

    fn set_holdout(&self, qa_pair_ids: &[i64]) -> Result<(), ImproveError> {
        let tx = self.conn.unchecked_transaction().map_err(db_err)?;
        tx.execute("DELETE FROM holdout_member", [])
            .map_err(db_err)?;
        {
            let mut stmt = tx
                .prepare("INSERT OR IGNORE INTO holdout_member(qa_pair_id) VALUES (?1)")
                .map_err(db_err)?;
            for id in qa_pair_ids {
                stmt.execute(params![id]).map_err(db_err)?;
            }
        }
        tx.commit().map_err(db_err)?;
        Ok(())
    }

    fn holdout_ids(&self) -> Result<Vec<i64>, ImproveError> {
        let mut stmt = self
            .conn
            .prepare("SELECT qa_pair_id FROM holdout_member ORDER BY qa_pair_id")
            .map_err(db_err)?;
        let rows = stmt
            .query_map([], |row| row.get::<_, i64>(0))
            .map_err(db_err)?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row.map_err(db_err)?);
        }
        Ok(out)
    }
}

// ---------------------------------------------------------------------------
// ReplyModel (평가용 로컬 답변 생성 시임)
// ---------------------------------------------------------------------------

/// 한 구성으로 한 질문에 답을 만드는 로컬 시임. 실사용은 로컬 임베더/모델을,
/// 테스트는 결정적 가짜를 주입한다. 외부 네트워크를 쓰지 않는다 (R8.15, R12.9).
pub trait ReplyModel {
    /// `knobs` 구성으로 `sample`에 대한 답을 만든다.
    fn generate(&self, knobs: &Knobs, sample: &QaSample) -> GeneratedReply;
}

/// [`ReplyModel::generate`]가 돌려주는 답과 측정 지연.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GeneratedReply {
    /// 생성된 답변 텍스트(메모리 전용, 저장하지 않음).
    pub text: String,
    /// 측정 지연(밀리초).
    pub latency_ms: u64,
}

/// 정렬된 지연 배열의 백분위(선형 보간 없는 최근접 순위).
fn percentile(sorted: &[u64], p: f64) -> u64 {
    if sorted.is_empty() {
        return 0;
    }
    let idx = ((p / 100.0) * (sorted.len() as f64 - 1.0)).round() as usize;
    sorted[idx.min(sorted.len() - 1)]
}

// ---------------------------------------------------------------------------
// SelfImprover
// ---------------------------------------------------------------------------

/// 자기개선기 (R8). 감지·진단·평가·승격·감시·롤백을 조율한다.
///
/// 품질 점수는 [`crate::experiment::score_reply`](로컬)로 계산하고, 전 과정에
/// [`NetworkPort`](주입된 [`crate::fakes::ForbiddenNetwork`])를 두어 외부 송신이
/// 0임을 하네스가 단정할 수 있게 한다 (R8.15, R12.9).
pub struct SelfImprover<'a> {
    store: &'a dyn ImproveStore,
    knobs: &'a dyn KnobStore,
    model: &'a dyn ReplyModel,
    net: &'a dyn NetworkPort,
    clock: &'a dyn Clock,
    style: StyleTarget,
    drift_threshold: f32,
    /// 승격 감시 상태(승격 직전 20건 평균, 승격 전 세대, 승격된 구성 다이제스트).
    watch: Option<WatchState>,
}

/// 승격 후 감시에 필요한 상태 (R8.12).
#[derive(Debug, Clone)]
struct WatchState {
    baseline_mean: f32,
    prior_generation: KnobGeneration,
    promoted_digest: KnobsDigest,
    problem: Option<(String, SignalKind)>,
}

impl<'a> SelfImprover<'a> {
    /// 자기개선기를 조립한다.
    pub fn new(
        store: &'a dyn ImproveStore,
        knobs: &'a dyn KnobStore,
        model: &'a dyn ReplyModel,
        net: &'a dyn NetworkPort,
        clock: &'a dyn Clock,
        style: StyleTarget,
        drift_threshold_cfg: Option<f32>,
    ) -> Self {
        Self {
            store,
            knobs,
            model,
            net,
            clock,
            style,
            drift_threshold: resolve_drift_threshold(drift_threshold_cfg),
            watch: None,
        }
    }

    /// 감지·진단·평가·승격·롤백 중 관측된 외부 송신 시도 수. 전 과정에서 0이어야
    /// 한다 (R8.15, R12.9).
    pub fn net_egress(&self) -> usize {
        self.net.egress_count()
    }

    /// 한 답변 관측에서 문제 신호를 감지·기록하고 감지된 신호를 돌려준다 (R8.1).
    pub fn observe(&self, obs: &ReplyObservation) -> Result<Vec<SignalRecord>, ImproveError> {
        let detected = detect_signals(obs, self.drift_threshold);
        let mut records = Vec::with_capacity(detected.len());
        for (kind, threshold_code) in detected {
            let record = SignalRecord {
                kind,
                room_pid: obs.room_pid.clone(),
                at: obs.at,
                threshold_code,
                dismissed: false,
            };
            self.store.record_signal(&record)?;
            records.push(record);
        }
        Ok(records)
    }

    /// 개선 착수 여부 (R8.3, R8.4).
    ///
    /// 최근 24시간 내 오탐 아닌 신호 중, 오탐 표시가 3회 이상인 종류를 제외하고,
    /// 동종 2건 이상 또는 이종 2종 이상이면 착수한다.
    pub fn should_start(&self, room_pid: &str) -> Result<bool, ImproveError> {
        let now = self.clock.now_ms();
        let since = now.saturating_sub(OBSERVE_WINDOW_MS);
        let signals = self.store.active_signals_since(room_pid, since)?;

        let mut by_kind: BTreeMap<SignalKind, u32> = BTreeMap::new();
        for s in &signals {
            let fp = self.store.false_positive_count(room_pid, s.kind)?;
            if fp >= FALSE_POSITIVE_CAP {
                continue; // 오탐 3회 이상 종류는 착수 판단에서 제외 (R8.4).
            }
            *by_kind.entry(s.kind).or_insert(0) += 1;
        }
        let same_kind_two = by_kind.values().any(|&c| c >= 2);
        let distinct_two = by_kind.len() >= 2;
        Ok(same_kind_two || distinct_two)
    }

    /// 신호를 오탐으로 표시하고 새 오탐 횟수를 돌려준다 (R8.4).
    pub fn dismiss(&self, room_pid: &str, kind: SignalKind) -> Result<u32, ImproveError> {
        self.store.dismiss(room_pid, kind)
    }

    /// 누적 신호를 원인 후보로 분류한다 (R8.5). 결과는 항상 비어 있지 않고 네
    /// 항목의 부분집합이다.
    pub fn diagnose(&self, sigs: &[SignalRecord]) -> Diagnosis {
        let mut set: BTreeSet<KnobKey> = BTreeSet::new();
        for s in sigs {
            for key in causes_for(s.kind) {
                set.insert(*key);
            }
        }
        // 신호가 없어도 착수했다면 최소 하나는 있어야 한다: 프롬프트 버전을 기본
        // 후보로 둔다.
        if set.is_empty() {
            set.insert(KnobKey::PromptVersion);
        }
        Diagnosis {
            causes: set.into_iter().collect(),
        }
    }

    /// 후보 구성을 홀드아웃 전체로 평가한다 (R8.9). 외부 네트워크를 쓰지 않는다.
    pub fn evaluate(&self, cand: &Knobs, holdout: &HoldoutSet) -> EvalSummary {
        let mut scores = BTreeMap::new();
        let mut latencies = Vec::with_capacity(holdout.items.len());
        let mut sum: u64 = 0;
        for sample in &holdout.items {
            let generated = self.model.generate(cand, sample);
            let score = score_reply(&self.style, &generated.text);
            scores.insert(sample.sample_pid.clone(), score);
            sum += score as u64;
            latencies.push(generated.latency_ms);
        }
        let evaluated = holdout.items.len();
        let mean_quality = if evaluated == 0 {
            0.0
        } else {
            sum as f32 / evaluated as f32
        };
        latencies.sort_unstable();
        let p95_latency_ms = percentile(&latencies, 95.0);
        EvalSummary {
            mean_quality,
            p95_latency_ms,
            evaluated,
            scores,
        }
    }

    /// 승격을 시도한다 (R8.9, R8.11, R8.14, R8.18).
    ///
    /// `problem`은 시도 횟수를 세는 문제 유형(방·종류), `current_eval`/`cand_eval`은
    /// 현재/후보 구성의 홀드아웃 평가, `holdout_len`은 홀드아웃 크기,
    /// `baseline_recent20`은 승격 직전 실사용 최근 20건 점수(자동 롤백 기준)다.
    pub fn promote(
        &mut self,
        problem: (String, SignalKind),
        cand: &Knobs,
        current_eval: &EvalSummary,
        cand_eval: &EvalSummary,
        holdout_len: usize,
        baseline_recent20: &[u8],
    ) -> Result<PromotionOutcome, ImproveError> {
        let (room_pid, kind) = problem.clone();

        // 홀드아웃 부족은 평가·승격 없이 현재 구성 유지, 시도로 세지 않음 (R8.18).
        if holdout_len < MIN_HOLDOUT_FOR_EVAL {
            return Ok(PromotionOutcome::Insufficient {
                have: holdout_len,
                need: MIN_HOLDOUT_FOR_EVAL,
            });
        }

        let digest = cand.digest();
        let rolled_back = self.store.rolled_back_digests()?;
        let attempts = self.store.attempts(&room_pid, kind)?;

        match promotion_gate(
            current_eval,
            cand_eval,
            holdout_len,
            &rolled_back,
            &digest,
            attempts,
        ) {
            Err(reason) => {
                // 거부로 끝난 시도는 1회로 센다 (R8.14). 홀드아웃 부족은 위에서
                // 이미 걸러졌다.
                self.store.bump_attempt(&room_pid, kind)?;
                self.store.record_history(&HistoryEntry {
                    at: self.clock.now_ms(),
                    problem_kind: kind.as_str().to_string(),
                    changed_keys: String::new(),
                    verdict: format!("rejected:{}", reason.code()),
                    generation: None,
                })?;
                Ok(PromotionOutcome::Rejected(reason))
            }
            Ok(()) => {
                let (prior_gen, prior_knobs) = self.knobs.current();
                let changed_keys = prior_knobs.changed_keys(cand);
                let generation = self.knobs.apply(cand.clone())?;
                let at = self.clock.now_ms();
                let holdout_mean = cand_eval.mean_quality.round().clamp(0.0, 100.0) as u8;

                self.store.record_generation(&GenerationRecord {
                    generation,
                    knobs: cand.clone(),
                    digest: digest.clone(),
                    promoted_at: at,
                    holdout_mean,
                })?;
                self.store.record_history(&HistoryEntry {
                    at,
                    problem_kind: kind.as_str().to_string(),
                    changed_keys: changed_keys
                        .iter()
                        .map(|k| k.as_str())
                        .collect::<Vec<_>>()
                        .join(","),
                    verdict: "promoted".to_string(),
                    generation: Some(generation),
                })?;

                let baseline_mean = mean_u8(baseline_recent20);
                self.watch = Some(WatchState {
                    baseline_mean,
                    prior_generation: prior_gen,
                    promoted_digest: digest,
                    problem: Some((room_pid, kind)),
                });

                Ok(PromotionOutcome::Promoted {
                    generation,
                    changed_keys,
                    at,
                    delta: cand_eval.mean_quality - current_eval.mean_quality,
                })
            }
        }
    }

    /// 승격 후 실사용 20건을 감시하고, 승격 직전 20건 평균보다 5점 이상 낮으면
    /// 자동으로 되돌린다 (R8.12).
    pub fn watch_after_promotion(
        &mut self,
        recent20: &[u8],
    ) -> Result<Option<RollbackOutcome>, ImproveError> {
        let Some(watch) = self.watch.clone() else {
            return Ok(None);
        };
        if recent20.len() < WATCH_WINDOW {
            return Ok(None); // 아직 20건이 쌓이지 않음.
        }
        let after_mean = mean_u8(&recent20[recent20.len() - WATCH_WINDOW..]);
        if after_mean >= watch.baseline_mean - ROLLBACK_DROP {
            return Ok(None); // 품질이 충분히 유지됨.
        }

        // 자동 롤백: 승격된 구성 다이제스트를 롤백 이력에 남기고(재승격 거부),
        // 승격 직전 세대로 되돌린다 (R8.19).
        self.store
            .record_rolled_back(&watch.promoted_digest, self.clock.now_ms())?;
        let outcome = self.apply_generation(watch.prior_generation, "rolled_back")?;

        // 롤백으로 끝난 시도는 1회로 센다 (R8.14).
        if let Some((room_pid, kind)) = &watch.problem {
            self.store.bump_attempt(room_pid, *kind)?;
        }
        self.watch = None;

        Ok(Some(RollbackOutcome {
            generation: outcome,
            before_mean: watch.baseline_mean,
            after_mean,
        }))
    }

    /// 보관된 승격 이전 구성 하나를 골라 되돌린다 (R8.13). 사용자 명시 동작이며
    /// 안전설정을 바꾸지 않는다.
    pub fn rollback_to(&mut self, gen: KnobGeneration) -> Result<KnobGeneration, ImproveError> {
        let applied = self.apply_generation(gen, "rolled_back")?;
        // 수동 되돌리기는 감시 상태를 해제한다.
        self.watch = None;
        Ok(applied)
    }

    /// 세대 `gen`의 구성을 실사용 기본으로 적용하고 이력을 남긴다.
    fn apply_generation(
        &self,
        gen: KnobGeneration,
        verdict: &str,
    ) -> Result<KnobGeneration, ImproveError> {
        // 먼저 KnobStore의 인메모리 세대에서 찾고, 없으면 지속 저장소에서 찾는다.
        let knobs = self
            .knobs
            .generations(KEEP_GENERATIONS)?
            .into_iter()
            .find(|g| g.generation == gen)
            .map(|g| g.knobs);
        let knobs = match knobs {
            Some(k) => k,
            None => {
                self.store
                    .generation(gen)?
                    .ok_or(ImproveError::GenerationNotFound(gen))?
                    .knobs
            }
        };
        let new_gen = self.knobs.apply(knobs)?;
        self.store.record_history(&HistoryEntry {
            at: self.clock.now_ms(),
            problem_kind: String::new(),
            changed_keys: String::new(),
            verdict: verdict.to_string(),
            generation: Some(new_gen),
        })?;
        Ok(new_gen)
    }
}

/// u8 점수 슬라이스의 평균(빈 슬라이스는 0.0).
fn mean_u8(scores: &[u8]) -> f32 {
    if scores.is_empty() {
        return 0.0;
    }
    let sum: u64 = scores.iter().map(|&s| s as u64).sum();
    sum as f32 / scores.len() as f32
}

#[cfg(test)]
mod tests;
