## Abstract

We have at hand a seed URL, and an Intent. Large Scale Scraping Bots dont worry for a seed url for the fact that they act primarily to index the internet and serve as a internet. We are not trying to build such a large scraping bot, but we want to build something that goes very deep (understands links, extracts documents, media, context etc, typically more effective in storage compared to chatgpt/perplexity/google), differentiating ourselves from large scale bots by going deep into extraction and understanding, given a seed URL and context.

We want to build a data pipeline to extract relevant documents about the page as a whole starting from the intent(text/object not sure what it is) and the seed URL (Here it is CNBC).

We identify the links with a certain confidence to click on it, then further go through the link and immediately upon clicking on it, we perform a 'understanding' of the page to detect what the page is about -- whether it is the IR page or home page or some other page only in part of our goal.

After clicking on any such link, the check happens to make sure with a certain confidence by 'understanding' the content, that a certain page is a IR page/home page/ other page! 
