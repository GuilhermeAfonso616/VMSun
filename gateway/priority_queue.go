package main

type StartPriorityQueue []*StartJob

func (pq StartPriorityQueue) Len() int { return len(pq) }

func (pq StartPriorityQueue) Less(i, j int) bool {
	if pq[i].Priority != pq[j].Priority {
		return pq[i].Priority > pq[j].Priority
	}
	return pq[i].sequence < pq[j].sequence
}

func (pq StartPriorityQueue) Swap(i, j int) {
	pq[i], pq[j] = pq[j], pq[i]
	pq[i].index = i
	pq[j].index = j
}

func (pq *StartPriorityQueue) Push(x any) {
	job := x.(*StartJob)
	job.index = len(*pq)
	*pq = append(*pq, job)
}

func (pq *StartPriorityQueue) Pop() any {
	old := *pq
	n := len(old)
	job := old[n-1]
	old[n-1] = nil
	job.index = -1
	*pq = old[0 : n-1]
	return job
}
