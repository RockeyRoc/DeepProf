#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
base_dir <- if (length(args) >= 1) normalizePath(args[[1]], mustWork = TRUE) else normalizePath("docs/experiments", mustWork = TRUE)
data_file <- file.path(base_dir, "source-data", "F09-m3-offline-summary.csv")
source_dir <- file.path(base_dir, "source-data")
figure_dir <- file.path(base_dir, "figures")
dir.create(figure_dir, recursive = TRUE, showWarnings = FALSE)

suppressPackageStartupMessages(library(ggplot2))
suppressPackageStartupMessages(library(patchwork))
suppressPackageStartupMessages(library(jsonlite))
suppressPackageStartupMessages(library(svglite))
suppressPackageStartupMessages(library(ragg))

data <- read.csv(data_file, fileEncoding = "UTF-8", stringsAsFactors = FALSE, check.names = FALSE)
offline_evidence <- fromJSON(file.path(source_dir, "M3-offline-summary-redacted.json"),
                             simplifyVector = TRUE)
summary <- offline_evidence$summary
matrix_data <- subset(data, metric == "matrix_coverage")
if (nrow(data) != 12L || sum(matrix_data$observed) != 400L || sum(matrix_data$planned) != 400L ||
    sum(data$observed[data$metric == "agreement" & data$series == "decision_completeness"]) != 520L ||
    summary$main_matrix$observed != 120L || summary$replay_matrix$observed != 120L ||
    summary$rag_ablation_matrix$observed != 160L ||
    summary$metrics$expected_action_family_match$numerator != 196L ||
    summary$metrics$expected_action_family_match$denominator != 280L ||
    summary$metrics$replay_decision_agreement$numerator != 120L ||
    summary$metrics$decision_completeness$numerator != 520L ||
    summary$metrics$bkt_prediction$n != 120L ||
    summary$metrics$evidence_gap_behavior$retrieval_absent_constraint_on_blocks != 40L ||
    summary$metrics$evidence_gap_behavior$retrieval_absent_constraint_off_generated != 26L) {
  stop("F09 data must match the frozen M3 offline run summary and denominators.")
}

palette <- c("#315D78", "#477A66", "#C87537", "#71889A")
theme_f09 <- theme_minimal(base_size = 8.3, base_family = "sans") +
  theme(
    plot.title = element_text(face = "bold", size = 9.4, colour = "#243746"),
    plot.subtitle = element_text(size = 7.3, colour = "#5D6870", margin = margin(b = 7)),
    axis.title = element_text(size = 7.2, colour = "#34424A"),
    axis.text = element_text(size = 7, colour = "#34424A"),
    axis.title.y = element_blank(),
    panel.grid.minor = element_blank(),
    panel.grid.major.y = element_blank(),
    legend.position = "none",
    plot.tag = element_text(face = "bold", size = 9, colour = "#243746"),
    plot.caption = element_text(size = 6.6, colour = "#5D6870", hjust = 0, lineheight = 1.05),
    plot.margin = margin(5, 9, 7, 5)
  )

matrix_data <- subset(data, metric == "matrix_coverage")
matrix_data$series <- factor(matrix_data$series, levels = rev(c("Main A", "Main B", "Main C", "Replay", "RAG ablation")))
p1 <- ggplot(matrix_data, aes(x = rate * 100, y = series, fill = series)) +
  geom_col(width = 0.62, colour = "white", linewidth = 0.25) +
  geom_text(aes(label = paste0(observed, "/", planned)), hjust = 1.08, size = 2.6, colour = "white") +
  scale_fill_manual(values = rep(palette, length.out = nlevels(matrix_data$series))) +
  scale_x_continuous(limits = c(0, 125), breaks = c(0, 25, 50, 75, 100),
                     labels = function(x) paste0(x, "%"),
                     expand = expansion(mult = c(0, 0))) +
  labs(title = "Offline evaluation matrices", subtitle = "Completed / planned fixed developer-fixture cells", x = "Matrix completion", tag = "A") +
  theme_f09 + theme(axis.text.y = element_text(size = 7))

agreement <- subset(data, metric == "agreement")
agreement$series <- factor(agreement$series,
  levels = c("action_family_match", "replay_decision_agreement", "decision_completeness", "locatable_reference"),
  labels = c("Action-family match", "Replay agreement", "Decision completeness", "Locatable references"))
agreement$series <- factor(agreement$series, levels = rev(levels(agreement$series)))
agreement$label_x <- ifelse(agreement$rate >= 0.95, agreement$rate * 100 - 2, agreement$rate * 100 + 2)
agreement$label_hjust <- ifelse(agreement$rate >= 0.95, 1, 0)
p2 <- ggplot(agreement, aes(x = rate * 100, y = series, colour = series)) +
  geom_segment(aes(x = 0, xend = rate * 100, yend = series), linewidth = 2.2, alpha = 0.35) +
  geom_point(size = 3.0) +
  geom_text(aes(x = label_x, hjust = label_hjust,
                label = paste0(observed, "/", planned, " (", round(rate * 100), "%)")),
            nudge_y = 0.27, size = 2.45, colour = "#243746") +
  scale_colour_manual(values = rep(palette, length.out = nlevels(agreement$series))) +
  scale_x_continuous(limits = c(0, 140), breaks = c(0, 25, 50, 75, 100), labels = function(x) paste0(x, "%")) +
  labs(title = "Automated strategy and replay checks", subtitle = "Separate denominators; not teacher ratings", x = NULL, tag = "B") +
  theme_f09 + theme(axis.text.y = element_text(size = 6.8), panel.grid.major.x = element_blank())

bkt <- subset(data, metric == "bkt_auc")
if (nrow(bkt) != 1L) stop("F09 requires one BKT prediction record.")
bkt_metrics <- sprintf("Brier %.3f   |   Log loss %.3f   |   ECE %.3f\nn=120 (70 positive; 50 negative)",
                       bkt$secondary_1, bkt$secondary_2, bkt$secondary_3)
p3 <- ggplot(bkt, aes(x = rate, y = "BKT discrimination")) +
  geom_vline(xintercept = 0.5, colour = "#7A858D", linetype = "dashed", linewidth = 0.45) +
  geom_segment(aes(x = 0, xend = rate, yend = "BKT discrimination"), colour = "#C87537", linewidth = 1.4) +
  geom_point(shape = 21, size = 4.2, stroke = 1, fill = "#C87537", colour = "white") +
  geom_text(aes(label = sprintf("AUC %.3f", rate)), nudge_x = -0.075, nudge_y = 0.25, hjust = 1, size = 2.8,
            colour = "#243746") +
  scale_x_continuous(limits = c(0, 1), breaks = c(0, 0.25, 0.5, 0.75, 1),
                     labels = scales::label_number(accuracy = 0.01)) +
  labs(title = "BKT prediction", subtitle = bkt_metrics, x = "AUC (dashed line = 0.5 reference)", tag = "C") +
  theme_f09 + theme(axis.text.y = element_blank(), axis.ticks.y = element_blank(),
                    panel.grid.major.x = element_blank(),
                    plot.subtitle = element_text(size = 6.2, lineheight = 1.15, margin = margin(b = 7)))

gaps <- subset(data, metric == "evidence_gap")
gaps$series <- factor(gaps$series,
  levels = c("constraint_blocks", "constraint_off_generates"),
  labels = c("Constraint on: blocked", "Constraint off: generated"))
gaps$series <- factor(gaps$series, levels = rev(levels(gaps$series)))
p4 <- ggplot(gaps, aes(x = observed, y = series, fill = series)) +
  geom_col(width = 0.55, colour = "white", linewidth = 0.25) +
  geom_text(aes(label = paste0(observed, "/", planned)), hjust = 1.08, size = 2.7, colour = "white") +
  scale_fill_manual(values = c("#C87537", "#71889A")) +
  scale_x_continuous(limits = c(0, 48), breaks = c(0, 10, 20, 30, 40)) +
  labs(title = "Evidence-gap behavior", subtitle = "40 constructed cells; blocked versus generated", x = "Case cells", tag = "D") +
  theme_f09 + theme(axis.text.y = element_text(size = 6.8), panel.grid.major.x = element_blank())

figure <- patchwork::wrap_plots(p1, p2, p3, p4, ncol = 2) +
  plot_annotation(
    title = "M3 offline evaluation: complete execution, poor BKT prediction",
    subtitle = "run_id m3-offline-20260924-final6 | Fake Provider | constructed developer cases | no human participants",
    caption = "Engineering path checks only. Action match is not teacher rating; locatable references do not prove semantic support.\nBKT uses unfit development parameters. Ablation records path behavior, not answer correctness.",
    theme = theme(
      plot.title = element_text(face = "bold", size = 12, colour = "#243746", hjust = 0),
      plot.subtitle = element_text(size = 8.4, colour = "#5D6870", hjust = 0, margin = margin(b = 6)),
      plot.caption = element_text(size = 7, colour = "#5D6870", hjust = 0, lineheight = 1.05, margin = margin(t = 7)),
      plot.margin = margin(5, 6, 4, 6)
    )
  )

skill_root <- Sys.getenv("NATURE_FIGURE_SKILL_ROOT", file.path(Sys.getenv("USERPROFILE"), ".agents", "skills", "nature-figure"))
font_family <- "sans"
alignment_helper <- file.path(skill_root, "scripts", "panel_alignment.R")
alignment_auditor <- file.path(skill_root, "scripts", "audit_panel_alignment.py")
adapter <- file.path(dirname(dirname(base_dir)), "scripts", "patchwork_panel_alignment_adapter.R")
if (!file.exists(alignment_helper) || !file.exists(alignment_auditor) || !file.exists(adapter)) {
  stop("The saved R figure backend or multi-panel alignment helpers are not available.")
}
source(alignment_helper)
source(adapter)

stem <- file.path(figure_dir, "F09-m3-offline-results")
width_in <- 183 / 25.4
height_in <- 142 / 25.4
require_patchwork_panel_alignment(
  figure,
  manifest_path = paste0(stem, ".alignment-layout.json"),
  report_path = paste0(stem, ".alignment.json"),
  width_in = width_in,
  height_in = height_in,
  panel_ids = c("a", "b", "c", "d"),
  row_groups = list(c("a", "b"), c("c", "d")),
  column_groups = list(c("a", "c"), c("b", "d")),
  audit_script = alignment_auditor,
  overlay_svg = paste0(stem, ".alignment.svg"),
  strict = TRUE
)

ragg::agg_png(paste0(stem, ".png"), width = width_in, height = height_in, units = "in", res = 450, background = "white")
print(figure); grDevices::dev.off()
ragg::agg_tiff(paste0(stem, ".tiff"), width = width_in, height = height_in, units = "in", res = 600,
               compression = "lzw", background = "white")
print(figure); grDevices::dev.off()
svglite::svglite(paste0(stem, ".svg"), width = width_in, height = height_in, bg = "white")
print(figure); grDevices::dev.off()
grDevices::pdf(paste0(stem, ".pdf"), width = width_in, height = height_in,
               family = "Helvetica", useDingbats = FALSE, bg = "white")
print(figure); grDevices::dev.off()
writeLines(capture.output(sessionInfo()), file.path(source_dir, "F09-R-session-info.txt"), useBytes = TRUE)
cat("Rendered F09 M3 offline evidence figure.\n")
