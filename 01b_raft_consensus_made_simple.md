# Raft Consensus Algorithm: Made Simple & Visual
## Step-by-Step Guide (Inspired by *The Secret Lives of Data*)

> **Interactive Reference**: [The Secret Lives of Data - Raft](https://thesecretlivesofdata.com/raft/)  
> **Purpose**: Master Raft consensus from absolute first principles to understand how `etcd` keeps Kubernetes consistent and crash-proof.

---

## 1. The Core Problem: Why Do We Need Raft?

### The 1-Server Problem (Single Point of Failure)
If you have **1 server** storing state (like a database):
```
Client  ──(SET x = 5)──►  [ Server A ] (x = 5)
```
* **Pros**: Simple! No disagreements.
* **Cons**: If Server A catches fire or loses power, **your entire system goes down**.

---

### The Naive Multi-Server Problem (Split Brain & Desync)
If you add 3 servers to prevent downtime, how do you keep them identical?
```
              ┌──► [ Server A ]  (x = 5)  ✓
Client (x=5) ─┼──► [ Server B ]  (x = 5)  ✓
              └──► [ Server C ]  (CRASHED or Slow Network)  ✗ (x = old_val)
```
* If Server C comes back online, what is the value of `x`?
* What if Client 1 writes `x = 5` to Server A, while Client 2 writes `x = 10` to Server B at the exact same millisecond?

This is the **Distributed Consensus Problem**: **How do multiple independent nodes agree on values over an unreliable network?**

Raft solves this with **Consensus via a Strong Leader**.

---

## 2. The 3 States of a Raft Node

At any given second, every node in a Raft cluster (e.g., your 3 Kubernetes master `etcd` nodes) is in **one of three states**:

```
        ┌─────────────────┐
        │    FOLLOWER     │ ◄── (All nodes start here)
        └────────┬────────┘
                 │ (Election timeout fires: "I haven't heard from a leader!")
                 ▼
        ┌─────────────────┐
        │    CANDIDATE    │ ◄── (Votes for itself, asks peers for votes)
        └────────┬────────┘
                 │ (Receives votes from a Majority / Quorum)
                 ▼
        ┌─────────────────┐
        │     LEADER      │ ◄── (Handles ALL client writes, sends heartbeats)
        └─────────────────┘
```

| State | What It Does | Who Talks To It? |
| :--- | :--- | :--- |
| **Follower** | Completely passive. Does not make decisions. Simply responds to incoming requests from Leaders or Candidates. | Listens to Leader heartbeats. |
| **Candidate** | Trying to get elected. Increments term number, votes for itself, and asks others to vote for it. | Talks to all peer nodes (`RequestVote`). |
| **Leader** | In charge of the entire cluster. Accepts all client writes, replicates data, and sends regular heartbeats. | Talks to clients and all Followers (`AppendEntries`). |

---

## 3. Step 1: Leader Election (How a Leader is Chosen)

### Concept A: The Election Timeout (The Countdown Timer)
* Every node starts as a **Follower**.
* Each Follower has a countdown timer called the **Election Timeout**.
* **The Secret Trick**: The timeout is **randomized** on each node (usually between **150ms and 300ms**).
  * Node A timeout: `160ms`
  * Node B timeout: `240ms`
  * Node C timeout: `290ms`

```
Node A [timer: 160ms] ──► Hits 0ms FIRST! 💥
Node B [timer: 240ms] ...still counting down (80ms left)
Node C [timer: 290ms] ...still counting down (130ms left)
```

---

### Concept B: Becoming a Candidate & Requesting Votes
Because Node A's timer hit zero first without hearing a heartbeat:
1. Node A changes its state from **Follower $\rightarrow$ Candidate**.
2. Node A starts a new **Election Term** (e.g., `Term = 1`).
3. Node A votes for **itself** (Vote count = 1).
4. Node A sends a `RequestVote` message to Node B and Node C.

```
                  ┌─── [RequestVote (Term 1)] ───► [ Node B ] (Votes YES)
[ Node A (Candidate) ] 
(Votes: 1)        └─── [RequestVote (Term 1)] ───► [ Node C ] (Votes YES)
```

---

### Concept C: Voting Rules
When Node B and Node C receive the `RequestVote` message:
1. **Rule**: A node can only vote **ONCE per term** on a first-come, first-served basis.
2. If Node B hasn't voted in `Term 1` yet, it replies with a **Vote YES** to Node A.
3. Node B and Node C **reset their own election timeouts**.

---

### Concept D: Majority (Quorum) & Becoming Leader
* Node A receives 2 "YES" votes (from B and C) + 1 vote (itself) = **3 total votes**.
* **Quorum Formula**: $\lfloor N / 2 \rfloor + 1$
  * In a 3-node cluster, Quorum is $\lfloor 3 / 2 \rfloor + 1 = 2$.
  * In a 5-node cluster, Quorum is $\lfloor 5 / 2 \rfloor + 1 = 3$.
* Since Node A has a majority ($3 \ge 2$), **Node A becomes the LEADER for Term 1!**

```
             ┌─── [Heartbeat] ───► [ Node B (Follower) ] (resets timer)
[ Node A (LEADER) ]
             └─── [Heartbeat] ───► [ Node C (Follower) ] (resets timer)
```

* **Heartbeat Mechanism**:
  * Node A immediately starts sending periodic empty `AppendEntries` messages (called **Heartbeats**) every `50ms-100ms`.
  * Every time Node B and Node C receive a heartbeat, they **reset their election timers**.
  * As long as Node A stays alive and sends heartbeats, B and C will never become candidates.

---

### What if There is a Tie? (Split Vote)
What if Node A and Node B time out at the exact same millisecond?
```
Node A votes for itself (1 vote) ──► Asks C
Node B votes for itself (1 vote) ──► Asks C
```
* Node C can only vote once, so it votes for whichever message arrived 1 microsecond faster (say, Node A).
* Result:
  * Node A = 2 votes (Quorum achieved $\rightarrow$ Leader!)
  * Node B = 1 vote.
* Even in rare cases where an even-node cluster splits 50/50:
  * Neither candidate gets a majority.
  * Their election timers run out again (with newly randomized durations).
  * The node with the shorter random timeout immediately starts `Term 2` and wins.

---

## 4. Step 2: Log Replication (How Data is Written & Replicated)

Once a Leader is elected, **ALL writes from clients (e.g., `kube-apiserver`) MUST go to the Leader**.

Here is the exact step-by-step lifecycle of a write (e.g., `SET name = "amrit"`):

```
                                    Step 1: Write
                     Client ─────────────────────────────► [ Node A (Leader) ]
                                                            Log: [ name="amrit" (Uncommitted) ]
                                                                     │
                                    Step 2: AppendEntries            │
                                    ┌────────────────────────────────┤
                                    ▼                                ▼
                        [ Node B (Follower) ]            [ Node C (Follower) ]
                    Log: [ name="amrit" (Uncomm.) ]  Log: [ name="amrit" (Uncomm.) ]
                                    │                                │
                                    └────────────────────────────────┤
                                    Step 3: ACK (Success)            │
                                    ▼                                │
                        [ Node A (Leader) ] ◄────────────────────────┘
                        Log: [ name="amrit" (COMMITTED) ]
                                    │
                                    ├──────────────────────────► Client (Step 4: Return HTTP 200 OK)
                                    │
                                    Step 5: Next Heartbeat ("Commit index updated!")
                                    ├───► Node B (marks log COMMITTED)
                                    └───► Node C (marks log COMMITTED)
```

### Detailed Breakdown:

1. **Client sends request to Leader**:
   * Client asks: `SET /registry/pods/default/nginx = <data>`.
2. **Leader writes to its local log (Uncommitted)**:
   * The entry is in the Leader's log file (WAL), but it is **not yet applied** to the state machine database.
3. **Leader sends `AppendEntries` to Followers**:
   * The data entry is sent in the next message to Node B and Node C.
4. **Followers write to their local log (Uncommitted)**:
   * Node B and C save the entry to disk and reply with an **Acknowledgment (ACK)**.
5. **Leader commits the entry upon Quorum**:
   * Once the Leader gets ACKs from a majority (2 out of 3 nodes: itself + 1 follower), the entry is officially **COMMITTED**.
6. **Leader responds to the Client**:
   * Leader applies the entry to its database state machine and returns `HTTP 200 Success` to `kube-apiserver`.
7. **Followers commit on next Heartbeat**:
   * The Leader's next heartbeat includes the latest `leaderCommit` index.
   * Followers see this index and apply the entry to their own local state machine.

---

## 5. Step 3: Network Partitions & Split-Brain Healing

This is the most powerful feature of Raft. What happens when the network cable between data centers is cut?

### The Scenario: 5-Node Cluster Split into 2 Partitions

Imagine 5 nodes: `[Node A, Node B]` on Subnet 1, and `[Node C, Node D, Node E]` on Subnet 2.

```
       PARTITION 1 (Minority: 2 nodes)          PARTITION 2 (Majority: 3 nodes)
    ┌────────────────────────────────────┐    ┌────────────────────────────────────┐
    │  [ Node A (Old Leader - Term 1) ]  │    │           [ Node C ]               │
    │  [ Node B (Follower) ]             │ ⚡ │           [ Node D ]               │
    │                                    │ ⚡ │           [ Node E ]               │
    └────────────────────────────────────┘    └────────────────────────────────────┘
```

#### What happens in Partition 1 (Minority: 2 nodes):
* Client sends write `SET x = 1` to Node A.
* Node A replicates to Node B.
* Node A tries to reach quorum: $\lfloor 5 / 2 \rfloor + 1 = 3$.
* Node A only has 2 nodes (A + B). It **CANNOT reach Quorum (3)**!
* **Result**: The write **STAYS UNCOMMITTED** and is never confirmed to the client!

#### What happens in Partition 2 (Majority: 3 nodes):
* Nodes C, D, E stop receiving heartbeats from Node A.
* Node C's election timer runs out.
* Node C becomes a Candidate for **`Term 2`**.
* Node C requests votes from D and E $\rightarrow$ receives 3 votes ($3 \ge 3$ quorum!).
* **Node C becomes the new LEADER for Term 2!**
* Client sends write `SET x = 2` to Node C $\rightarrow$ replicated to D and E $\rightarrow$ **COMMITTED**!

---

### What Happens When the Network Heals?

```
                     NETWORK PARTITIONS RECONNECT!
    ┌────────────────────────────────────────────────────────────────────────┐
    │ [ Node A ] (Term 1, Uncommitted: x=1)    [ Node C (LEADER, Term 2) ]   │
    │ [ Node B ] (Term 1, Uncommitted: x=1)    [ Node D ] (Committed: x=2)   │
    │                                          [ Node E ] (Committed: x=2)   │
    └────────────────────────────────────────────────────────────────────────┘
```

1. Node A (Leader of Term 1) sees Node C's heartbeat with **`Term 2`**.
2. **The Golden Raft Rule**: A higher Term number **always wins**.
3. Node A immediately steps down from Leader to **Follower** and updates its term to `Term 2`.
4. Node A and B see that their uncommitted `x = 1` entry does not exist in Leader C's log.
5. **Rollback & Overwrite**: Nodes A and B roll back the uncommitted entry and overwrite their logs with Leader C's log (`x = 2`).
6. **Result**: All 5 nodes are now 100% synchronized with zero data corruption!

---

## 6. How Raft Directly Powers Kubernetes & RKE2 (`etcd`)

| Raft Concept | Implementation in Kubernetes / RKE2 |
| :--- | :--- |
| **Node** | An `etcd` instance running on each Master node (`master-1`, `master-2`, `master-3`). |
| **Client** | The `kube-apiserver` (Stateless REST API). |
| **Log Entry** | A serialized protobuf entry of a Kubernetes object (e.g. `/registry/pods/prod/api-pod`). |
| **WAL (Write-Ahead Log)** | On-disk append-only file stored in `/var/lib/rancher/rke2/server/db/etcd/member/wal/`. |
| **State Machine** | The BoltDB KV database file (`.../etcd/member/snap/db`). |
| **Election & Heartbeat Ports** | TCP Port `2380` (etcd peer communication). Client requests use TCP Port `2379`. |

---

## 7. Key Takeaways & Interview Cheat-Sheet

* **Why odd numbers of masters (3, 5, 7)?**
  * A 3-node cluster tolerates **1 failure** (needs 2).
  * A 4-node cluster also tolerates **only 1 failure** (needs 3). Adding the 4th node adds network overhead without increasing fault tolerance.
* **Can Followers serve writes?**
  * **No**. If a client writes to an etcd follower, the follower forwards the write request directly to the current Raft Leader.
* **What is a Quorum loss?**
  * If 2 out of 3 master nodes die, the remaining 1 node cannot reach quorum ($1 < 2$). etcd switches to **Read-Only mode**; no new pods, deployments, or changes can be written until a 2nd node recovers.
