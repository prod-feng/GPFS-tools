# GPFS-tools


## 1 gpfs_iops_mon.py

Rocky Linux 8, Python 3.6 compatible.

Using ```"mmpmon"``` command to check the IO performance of client nodes. NB: ```"mmpmon"``` command has a hard limit of ```96``` nodes at a time. With 400+ nodes, needs to 
make the ```"mmpmon"``` call in a batch, like ```"--batch-size 90"```, so process 90 nodes at onetime. 


Make a ```nodes.list``` file, contains the GPFS client nodes, all of them, or you want. You can run command:

```
/usr/lpp/mmfs/bin/mmlscluser
```
to list all these client nodes.

```
node001
node002
...

```
Now run:
```
sudo ./gpfs_iops_mon.py -n nodes.list -i 2 --per-node --batch-size 90  -o ./gpfs_iops_09-16-full.csv 
```

```
GPFS I/O MONITOR
2026-09-16 19:30:51   interval=3.0s   nodes=404/409   mmpmon=5
=============================================================================================================================
READ           26,629 IOPS       12,907.9 MB/s
WRITE         517,695 IOPS          349.9 MB/s
TOTAL         544,324 IOPS       13,257.9 MB/s
OPEN            4,837 ops/s
CLOSE           4,851 ops/s
READDIR         3,015 ops/s
INODE           1,820 ops/s
=============================================================================================================================
NODE                              READ        WRITE         TOTAL         MB/s       OPEN      CLOSE    READDIR        INODE
-----------------------------------------------------------------------------------------------------------------------------
node001                         3,196      245,773       248,968         52.5          0          0          0            1
node002                         3,140      239,591       242,731         52.8          0          0          0            2
node003                            31        5,974         6,005         48.2          0          0          0           18
node004                         3,670        1,608         5,278         68.2        548        602         62          208
node005.                        3,964            0         3,964      1,438.7        348        348        346          254
...

PAGE 1/12   sort=TOTAL   showing=35/404
n/p=page  a=alpha  r=read  w=write  t=total  b=MB/s
+/-=page size  f=filter  0=nonzero  x=clear  q=quit

```

Also, the details will be saved in a file: ```gpfs_iops.csv```
