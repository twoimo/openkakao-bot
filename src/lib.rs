pub mod ax_send;
pub mod auto_reply_service;
pub mod breaker;
pub mod collector;
pub mod context;
pub mod coverage;
pub mod dataset;
pub mod doctor;
pub mod durability;
pub mod error;
pub mod experiment;
pub mod fakes;
pub mod forward;
pub mod geeknews;
pub mod improve;
pub mod live_sample;
pub mod local_db;
pub mod logging;
pub mod loco;
pub mod memory;
#[allow(
    dead_code,
    reason = "the library ax_send module reuses the binary media validator for strict transcript binding"
)]
pub mod media;
pub mod message_db;
pub mod model;
pub mod model_config;
pub mod packaging;
pub mod ports;
pub mod profile;
pub mod rag_eval;
pub mod relay;
pub mod reply_policy;
pub mod room_catalog;
pub mod safety;
pub mod ui_shell;
pub mod metrics;
