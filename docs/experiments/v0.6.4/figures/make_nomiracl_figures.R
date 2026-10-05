# All officially available judged candidates; descriptive fixed-snapshot aggregates.
library(ggplot2)
library(svglite)
library(ragg)
library(jsonlite)
args <- commandArgs(trailingOnly=TRUE)
out <- if (length(args)) args[1] else 'docs/experiments/v0.6.4'
data_dir <- file.path(out, 'data')
fig_dir <- file.path(out, 'figures')
contract <- fromJSON(file.path(data_dir, 'nomiracl-figure-contract.json'))
theme_set(theme_classic(base_size=8, base_family='Arial') +
  theme(axis.text=element_text(size=8), axis.title=element_text(size=8),
        plot.title=element_text(size=10, face='bold'), plot.subtitle=element_text(size=8),
        plot.caption=element_text(size=7, hjust=0), legend.position='top', legend.title=element_blank(),
        legend.text=element_text(size=8), plot.margin=margin(8, 12, 8, 8)))
save_plot <- function(p, name) {
  stem <- file.path(fig_dir, name)
  svglite(paste0(stem, '.svg'), width=183/25.4, height=115/25.4, bg='white')
  print(p); dev.off()
  cairo_pdf(paste0(stem, '.pdf'), width=183/25.4, height=115/25.4, family='Arial', bg='white')
  print(p); dev.off()
  ggsave(paste0(stem, '.png'), p, device=agg_png, width=183, height=115, units='mm', dpi=600, bg='white')
}
r <- read.csv(file.path(data_dir, 'nomiracl-ranking-metrics.csv'), fileEncoding='UTF-8-BOM')
stopifnot(nrow(r)==8, all(is.finite(r$mean_ndcg5)), all(r$mean_ndcg5>=0 & r$mean_ndcg5<=1))
methods <- c('BGE-M3 dense', 'BM25', 'BGE-M3 + BM25 RRF-60', 'BGE reranker v2-m3')
r$method <- factor(r$method, levels=rev(methods))
r$split <- factor(r$split, levels=c('dev', 'test'), labels=c('Development (n=393)', 'Test (n=920)'))
p <- ggplot(r, aes(x=mean_ndcg5, y=method, colour=split, shape=split)) +
  geom_point(size=2.8, position=position_dodge(width=.55)) +
  geom_text(aes(label=sprintf('%.4f', mean_ndcg5)), hjust=-.30, size=2.9,
            position=position_dodge(width=.55), show.legend=FALSE) +
  scale_colour_manual(values=c('#8295A5', '#287D8E')) + scale_shape_manual(values=c(16, 17)) +
  scale_x_continuous(limits=c(0, 1.10), breaks=seq(0, 1, .2), expand=expansion(mult=c(.01,0))) +
  labs(title='NoMIRACL Chinese: fixed-candidate ranking',
       subtitle='All 3,770 queries and 37,599 available candidate pairs scored',
       x='Mean nDCG@5 over relevant queries', y=NULL,
       caption='Dev: 393 relevant queries; test: 920. Four methods use the same candidate pools.\n95 absent qrels (35 positive) filtered; no queries excluded. Single snapshot; no CI.\nCandidate ranking is not corpus-wide Recall or generated-answer accuracy.')
save_plot(p, 'figure-12-nomiracl-ranking')
g <- read.csv(file.path(data_dir, 'nomiracl-gate-metrics.csv'), fileEncoding='UTF-8-BOM')
stopifnot(nrow(g)==2, all(is.finite(g$value)), all(g$denominator==c(1375,920)))
g$metric <- factor(g$metric, levels=rev(g$metric))
g$label <- sprintf('%.2f%% | %d / %d', g$value*100, g$numerator, g$denominator)
p <- ggplot(g, aes(x=value, y=metric)) +
  geom_segment(aes(x=0, xend=value, yend=metric), linewidth=.6, colour='#BDCDD4') +
  geom_point(size=3, colour='#287D8E') +
  geom_text(aes(label=label), hjust=-.15, size=3, colour='#19324D') +
  scale_x_continuous(limits=c(0,1.18), breaks=seq(0,1,.2), labels=function(x) paste0(round(x*100),'%'),
                     expand=expansion(mult=c(.01,0))) +
  labs(title='NoMIRACL Chinese: frozen evidence gate on test',
       subtitle=sprintf('Dev-only threshold %.5f; test maximum-score AUC %.4f',contract$threshold,contract$test_auc),
       x='Fraction of questions in the corresponding relevance class', y=NULL,
       caption=sprintf('Accept when the maximum reranker score is at least the frozen threshold.\nDev FAR %.2f%% (cap 5%%); relevant-query acceptance %.2f%%. Ties choose the lower threshold.\nTest: 1,375 non-relevant queries (FAR); 920 relevant queries (FRR). Fixed-candidate gate; no CI.',
                       contract$dev_far*100,contract$dev_relevant_acceptance*100))
save_plot(p, 'figure-13-nomiracl-gate')
cat('Exported two independent plot areas as SVG/PDF/600-dpi PNG.\n')
