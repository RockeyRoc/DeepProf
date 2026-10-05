# New decision comparisons; inherit only the existing report's visual style.
library(ggplot2)
library(svglite)
library(ragg)
library(jsonlite)
args <- commandArgs(trailingOnly=TRUE)
out <- if (length(args)) args[1] else 'docs/experiments/v0.6.4'
data_dir <- file.path(out, 'data')
fig_dir <- file.path(out, 'figures')
contract <- fromJSON(file.path(data_dir, 'fulltext-ablation-figure-contract.json'))
splits <- c(historical='Old 40', development='Development 40', sealed_test='Sealed 40')
conditions <- c('retrieval-on_constraint-on'='R+ E+', 'retrieval-on_constraint-off'='R+ E-',
                'retrieval-off_constraint-on'='R- E+ (rule)', 'retrieval-off_constraint-off'='R- E-')
theme_set(theme_classic(base_size=8, base_family='Arial') +
  theme(axis.text=element_text(size=7), axis.title=element_text(size=8),
        plot.title=element_text(size=10, face='bold'), plot.subtitle=element_text(size=8),
        plot.caption=element_text(size=7, hjust=0), legend.position='top', legend.title=element_blank(),
        legend.text=element_text(size=7), plot.margin=margin(8, 10, 8, 8)))
save_plot <- function(p, name) {
  stem <- file.path(fig_dir, name)
  svglite(paste0(stem, '.svg'), width=183/25.4, height=115/25.4, bg='white')
  print(p); dev.off()
  cairo_pdf(paste0(stem, '.pdf'), width=183/25.4, height=115/25.4, family='Arial', bg='white')
  print(p); dev.off()
  ggsave(paste0(stem, '.png'), p, device=agg_png, width=183, height=115, units='mm', dpi=600, bg='white')
}
r <- read.csv(file.path(data_dir, 'fulltext-ablation-rates.csv'), fileEncoding='UTF-8-BOM')
stopifnot(nrow(r)==24, all(r$denominator>0), all(r$value>=0 & r$value<=1))
keys <- paste(r$condition, r$split)
accept <- setNames(paste0(r$numerator[r$metric=='false_accept_generation'], '/',
                         r$denominator[r$metric=='false_accept_generation']), keys[r$metric=='false_accept_generation'])
reject <- setNames(paste0(r$numerator[r$metric=='false_reject_no_generation'], '/',
                         r$denominator[r$metric=='false_reject_no_generation']), keys[r$metric=='false_reject_no_generation'])
r$display <- paste0(conditions[r$condition], ' | ', splits[r$split], ' | ', accept[keys], '; ', reject[keys])
r$label <- factor(r$display, levels=rev(unique(r$display)))
r$metric <- factor(r$metric, levels=c('false_accept_generation', 'false_reject_no_generation'),
                   labels=c('Insufficient: generated', 'Answerable: not generated'))
p <- ggplot(r, aes(x=value, y=label, colour=metric, shape=metric)) +
  geom_point(size=2.1, position=position_dodge(width=.36)) +
  scale_colour_manual(values=c('#A47552', '#287D8E')) + scale_shape_manual(values=c(16, 17)) +
  scale_x_continuous(limits=c(-.035, 1.035), breaks=seq(0, 1, .2), labels=function(x) paste0(round(x*100), '%')) +
  labs(title='Four-condition ablation: generation decision errors',
       subtitle='All 480 eligible cells; denominators follow AI answerability labels',
       x='Fraction of questions in the corresponding answerability class', y=NULL,
       caption=paste0('Row fractions: generated/insufficient; not generated/answerable. R: retrieval; E: evidence constraint.\n',
                      'R- E+ is a fixed no-generation rule. Decision outcomes are not answer correctness; no CI.\nBatch: ', contract$batch_id))
save_plot(p, 'figure-10-fulltext-ablation-decisions')
t <- read.csv(file.path(data_dir, 'fulltext-ablation-transitions.csv'), fileEncoding='UTF-8-BOM')
stopifnot(nrow(t)==48, all(t$denominator>0), sum(t$count)==240)
answerability <- c(insufficient_evidence='Insufficient', answerable='Answerable')
t$display <- paste0(ifelse(t$retrieval=='on', 'R+', 'R- (rule)'), ' | ', splits[t$split], ' | ',
                    answerability[t$answerability], ' | n=', t$denominator)
t$label <- factor(t$display, levels=rev(unique(t$display)))
t$transition <- factor(t$transition,
  levels=c('generated_both', 'generation_stopped', 'generation_started', 'neither_generated'),
  labels=c('Generated both', 'Stopped', 'Started', 'Neither'))
p <- ggplot(t, aes(x=value, y=label, fill=transition)) +
  geom_col(width=.63, position=position_stack(reverse=TRUE)) +
  geom_text(aes(label=ifelse(count>0, count, '')), position=position_stack(vjust=.5, reverse=TRUE),
            size=2.6, colour='#172D3D') +
  scale_fill_manual(values=c('#A7BBC8', '#E4B899', '#94C9CF', '#E2E7EB'), drop=FALSE) +
  scale_x_continuous(limits=c(0, 1), breaks=seq(0, 1, .2), labels=function(x) paste0(round(x*100), '%'),
                     expand=expansion(mult=c(0, .005))) +
  labs(title='Matched questions: switching the evidence constraint on',
       subtitle='Direction: E- to E+; 120 matched questions at each retrieval setting',
       x='Fraction within each row; numbers inside segments are question counts', y=NULL,
       caption=paste0('AI answerability strata; all 240 question pairs retained. No seed replicates or CI.\n',
                      'R- comparisons include the fixed E+ no-generation rule; changes are decision transitions, not accuracy gains.\nBatch: ', contract$batch_id))
save_plot(p, 'figure-11-fulltext-ablation-paired')
