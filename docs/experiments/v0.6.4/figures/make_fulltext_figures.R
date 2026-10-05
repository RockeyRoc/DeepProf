library(ggplot2)
library(svglite)
library(ragg)
library(jsonlite)
args <- commandArgs(trailingOnly=TRUE)
out <- if (length(args)) args[1] else 'docs/experiments/v0.6.4'
data_dir <- file.path(out, 'data')
fig_dir <- file.path(out, 'figures')
summary <- jsonlite::fromJSON(file.path(data_dir,'fulltext-rag-summary.json'))
eligible <- summary$formal_eligible_cells
observed <- summary$formal_observed_cells
planned <- summary$formal_planned_cells
preflight <- summary$budget$phase_requests$preflight
batch_caption <- paste('Batch:',summary$batch_id)
splits <- c(historical='Old 40', development='Development 40', sealed_test='Sealed 40')
conditions <- c('retrieval-on_constraint-on'='R+ E+', 'retrieval-on_constraint-off'='R+ E-',
                'retrieval-off_constraint-on'='R- E+', 'retrieval-off_constraint-off'='R- E-')
theme_set(theme_classic(base_size=8,base_family='Arial') +
  theme(axis.text=element_text(size=7),axis.title=element_text(size=8),
        plot.title=element_text(size=10,face='bold'),plot.subtitle=element_text(size=8),
        plot.caption=element_text(size=7,hjust=0),legend.position='top',legend.title=element_blank(),
        legend.text=element_text(size=7),plot.margin=margin(8,16,8,8)))
save_plot <- function(p,name) {
  stem <- file.path(fig_dir,name)
  svglite::svglite(paste0(stem,'.svg'),width=183/25.4,height=115/25.4,bg='white')
  print(p);dev.off()
  grDevices::cairo_pdf(paste0(stem,'.pdf'),width=183/25.4,height=115/25.4,family='Arial',bg='white')
  print(p);dev.off()
  ggsave(paste0(stem,'.png'),p,device=agg_png,width=183,height=115,units='mm',dpi=600,bg='white')
}
m <- read.csv(file.path(data_dir,'fulltext-matrix.csv'),check.names=FALSE,fileEncoding='UTF-8-BOM')
m$label <- factor(paste(conditions[m$condition],splits[m$split],sep='  |  '),
                  levels=rev(paste(conditions[m$condition],splits[m$split],sep='  |  ')))
p <- ggplot(m,aes(y=label)) +
  geom_col(aes(x=planned),fill='#DDE4EA',width=.65) +
  geom_col(aes(x=observed),fill='#A47552',width=.65) +
  geom_col(aes(x=valid),fill='#287D8E',width=.4) +
  geom_text(aes(x=41,label=paste0(valid,'/',observed,'/',planned,'; calls ',requests)),hjust=0,size=2.6) +
  scale_x_continuous(limits=c(0,65),breaks=c(0,10,20,30,40),expand=expansion(mult=0)) +
  labs(title='Full-text ablation: execution and eligibility',
       subtitle=paste0('Eligible / observed / planned: ',eligible,' / ',observed,' / ',planned),
       x='Formal cells',y=NULL,
       caption=paste0('R: retrieval; E: evidence constraint. Grey: planned; brown: observed; teal: eligible.\n',
                      'Labels: eligible/observed/planned; formal calls. Preflight attempts (',preflight,') are separate.\n',batch_caption))
save_plot(p,'figure-07-fulltext-matrix')
r <- read.csv(file.path(data_dir,'fulltext-retrieval.csv'),check.names=FALSE,fileEncoding='UTF-8-BOM')
if(nrow(r)) {
  r$display <- paste0(splits[r$split],'  |  ',r$method,'  |  n=',r$n)
  r$label <- factor(r$display,levels=rev(unique(r$display)))
  r$metric <- factor(r$metric,levels=c('recall_at_20','ndcg_at_5'),labels=c('Conditional Recall@20','nDCG@5'))
  p <- ggplot(r,aes(x=value,y=label,colour=metric,shape=metric)) +
    geom_point(data=subset(r,!is.na(value)),size=2.1,position=position_dodge(width=.35)) +
    geom_text(data=subset(r,is.na(value) & metric=='Conditional Recall@20'),aes(x=.96,label='N/A'),
              colour='#767676',size=2.5,hjust=1,show.legend=FALSE) +
    scale_colour_manual(values=c('#287D8E','#A47552'),drop=FALSE) + scale_shape_manual(values=c(16,17),drop=FALSE) +
    scale_x_continuous(limits=c(0,1),breaks=seq(0,1,.2)) +
    labs(title='Full-text retrieval quality',
         subtitle=if(all(is.na(r$value))) 'N/A: no completed semantic judgments' else 'Exploratory estimates from completed AI judgments',
         x='Point estimate',y=NULL,
         caption=paste('Recall uses judged pools. Row n: scored queries; unknown or zero-relevance pools excluded; no CI.',batch_caption,sep='\n'))
  if(all(is.na(r$value))) p <- p + theme(legend.position='none')
  save_plot(p,'figure-08-fulltext-retrieval')
}
c <- read.csv(file.path(data_dir,'fulltext-citation.csv'),check.names=FALSE,fileEncoding='UTF-8-BOM')
keys <- paste(c$condition,c$split)
pair_n <- setNames(c$denominator[c$metric=='single_support'],keys[c$metric=='single_support'])
claim_n <- setNames(c$denominator[c$metric=='claim_evidence_coverage'],keys[c$metric=='claim_evidence_coverage'])
c$display <- paste0(conditions[c$condition],'  |  ',splits[c$split],'  |  n=',pair_n[keys],'/',claim_n[keys])
c$label <- factor(c$display,levels=rev(unique(c$display)))
c$metric <- factor(c$metric,levels=c('single_support','claim_evidence_coverage'),labels=c('Citation pair support','Claim evidence coverage'))
p <- ggplot(c,aes(x=value,y=label,colour=metric,shape=metric)) +
  geom_point(data=subset(c,!is.na(value)),size=2.1,position=position_dodge(width=.35)) +
  geom_text(data=subset(c,is.na(value) & metric=='Citation pair support'),aes(x=.96,label='N/A'),
            inherit.aes=TRUE,colour='#767676',size=2.5,hjust=1,show.legend=FALSE) +
  scale_colour_manual(values=c('#287D8E','#A47552'),drop=FALSE) + scale_shape_manual(values=c(16,17),drop=FALSE) +
  scale_x_continuous(limits=c(0,1),breaks=seq(0,1,.2)) +
  labs(title='Citation support and answer evidence coverage',
       subtitle=if(all(is.na(c$value))) 'N/A: citation semantic judgments pending; formal execution is complete' else 'Exploratory pair and claim estimates from AI judgments',
       x='Fully supported fraction',y=NULL,
       caption=paste('Row n: evaluated citation pairs / claims. Distinct denominators; missing evaluations are N/A.',batch_caption,sep='\n'))
if(all(is.na(c$value))) p <- p + theme(legend.position='none')
save_plot(p,'figure-09-fulltext-citation')
