-- Immutable v1 migration asset: add a new version for future contract changes.
-- A BEFORE trigger covers ORM, bulk SQL, restores and explicit digest updates.
CREATE OR REPLACE FUNCTION radar_validation_projection_v1() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.validation_projection := jsonb_build_object(
        'close', NEW.input_snapshot #> '{ohlcv,close}',
        'support_level', NEW.input_snapshot #> '{indicators,support_level}',
        'resistance_level', NEW.input_snapshot #> '{indicators,resistance_level}',
        'ma20', NEW.input_snapshot #> '{indicators,ma20}',
        'ma60', NEW.input_snapshot #> '{indicators,ma60}',
        'versions', NEW.input_snapshot -> 'versions',
        'selection_version', NEW.input_snapshot -> 'selection_version',
        'market_regime', NEW.input_snapshot #> '{market_context,regime}',
        'candidate_benchmark', NEW.input_snapshot #> '{replay_input,market_context,benchmark,symbol}',
        'relative_freshness', NEW.score_breakdown #> '{relative_strength,freshness}',
        'relative_value', NEW.score_breakdown #> '{relative_strength,relative_value}',
        'score_market_regime', NEW.score_breakdown #> '{market_context,details,regime}'
    );
    NEW.validation_input_hash := md5(jsonb_build_array(
        NEW.id,
        NEW.run_id,
        NEW.symbol,
        NEW.primary_bucket,
        NEW.secondary_buckets,
        NEW.observation_score,
        NEW.bucket_scores,
        NEW.risk_labels,
        NEW.matched_rules,
        NEW.repeat_status,
        NEW.data_dates,
        NEW.selection_status,
        NEW.prefilter_status,
        NEW.prefilter_reasons,
        NEW.shadow_cohort,
        NEW.validation_projection
    )::text);
    RETURN NEW;
END;
$$;
CREATE TRIGGER radar_validation_projection_v1
BEFORE INSERT OR UPDATE ON daily_radar_candidates
FOR EACH ROW EXECUTE FUNCTION radar_validation_projection_v1();
