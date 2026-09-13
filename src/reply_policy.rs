use anyhow::Result;

/// Enforce the account owner's laughter preference for automatic replies.
///
/// This policy is intentionally separate from ordinary outbound validation:
/// operator-authored sends remain unrestricted, while authenticated automatic
/// reply paths fail closed without rewriting the intended message.
pub fn validate_auto_reply_laughter(message: &str) -> Result<()> {
    if message.contains('ㅎ') {
        anyhow::bail!("automatic reply violates the configured laughter policy");
    }

    let mut laughter_run = 0usize;
    for character in message.chars().chain(std::iter::once('\0')) {
        if character == 'ㅋ' {
            laughter_run += 1;
        } else {
            if (1..3).contains(&laughter_run) {
                anyhow::bail!("automatic reply violates the configured laughter policy");
            }
            laughter_run = 0;
        }
    }
    Ok(())
}

/// Light safety rules for the never-skip fallback.
///
/// Operator rule: an authorized sender is never skipped, so a draft that the
/// full policy refused may still go out when it clears the light rules:
/// - not empty
/// - not longer than 220 characters
/// - not a verbatim copy of the inbound message
pub fn validate_lenient_policy_draft(draft: &str, inbound: &str) -> Result<()> {
    let normalized_draft = draft.split_whitespace().collect::<Vec<_>>().join(" ");
    if normalized_draft.is_empty() {
        anyhow::bail!("draft is empty");
    }
    if normalized_draft.chars().count() > 220 {
        anyhow::bail!("draft exceeds 220 characters");
    }
    let normalized_inbound = inbound.split_whitespace().collect::<Vec<_>>().join(" ");
    if !normalized_inbound.is_empty() && normalized_draft == normalized_inbound {
        anyhow::bail!("draft verbatim echoes inbound message");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn forbids_hieuh_and_short_kieuk_runs() {
        for invalid in [
            "반가워ㅋ",
            "반가워ㅋㅋ",
            "반가워ㅎ",
            "반가워ㅎㅎ",
            "반가워ㅎㅎㅎ",
            "반가워ㅋㅋㅋ ㅎㅎㅎ",
            "ㅋ ㅋ ㅋ",
        ] {
            assert!(validate_auto_reply_laughter(invalid).is_err(), "{invalid}");
        }
        for valid in [
            "반가워",
            "반가워ㅋㅋㅋ",
            "반가워ㅋㅋㅋㅋ",
            "ㅋㅋㅋ ㅋㅋㅋㅋㅋ",
        ] {
            assert!(validate_auto_reply_laughter(valid).is_ok(), "{valid}");
        }
    }

    #[test]
    fn validates_lenient_policy_drafts() {
        assert!(validate_lenient_policy_draft("잘 쉬어", "오늘 휴가임").is_ok());
        assert!(validate_lenient_policy_draft("  ㅋㅋㅋ  ", "오늘 휴가임").is_ok());
        assert!(validate_lenient_policy_draft("", "오늘 휴가임").is_err());
        assert!(validate_lenient_policy_draft("   ", "오늘 휴가임").is_err());
        assert!(validate_lenient_policy_draft("오늘 휴가임", "오늘 휴가임").is_err());
        assert!(validate_lenient_policy_draft("  오늘   휴가임  ", "오늘 휴가임").is_err());
        let long_draft = "a".repeat(221);
        assert!(validate_lenient_policy_draft(&long_draft, "오늘 휴가임").is_err());
        let max_draft = "a".repeat(220);
        assert!(validate_lenient_policy_draft(&max_draft, "오늘 휴가임").is_ok());
    }
}
