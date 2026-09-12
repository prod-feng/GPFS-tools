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
./gpfs_iops_mon-dashboard-batch.py -n nodes.list -i 2 --per-node --batch-size 90
```

```
GPFS I/O MONITOR
2026-09-12 17:30:50   interval=2.0s   nodes=404/409   mmpmon=5
========================================================================================================================
READ           26,499 IOPS       19,521.9 MB/s
WRITE          12,396 IOPS          409.5 MB/s
TOTAL          38,895 IOPS       19,931.3 MB/s
========================================================================================================================
NODE                              READ        WRITE         TOTAL         MB/s
------------------------------------------------------------------------------------------------------------------------
node001                        11,804        3,211        15,016        117.3
node002                         5,199            0         5,199        666.3
node009                           375        4,583         4,958         97.2
....
------------------------------------------------------------------------------------------------------------------------
PAGE 1/12   sort=TOTAL   showing=35/404
n/p=page  a=alpha  r=read  w=write  t=total  b=MB/s
+/-=page size  f=filter  0=nonzero  x=clear  q=quit

```

Also, the details will be saved in a file: ```gpfs_iops.csv```
