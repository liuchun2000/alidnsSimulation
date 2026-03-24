模拟阿里云dns管理服务，可使用alidns sdk管理域名。需连接数据库和etcd，coredns连接etcd
```
pip install fastapi uvicorn etcd3 sqlalchemy pymysql
```
可能需要protobuf降版本
数据库schema dns_db
```
CREATE TABLE `aliyun_dns_record` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `record_id` VARCHAR(64) NOT NULL COMMENT '阿里云解析记录ID',
  `email` VARCHAR(128) DEFAULT NULL COMMENT '所属用户邮箱',
  `cluster_id` VARCHAR(64) DEFAULT NULL COMMENT '集群ID',
  `sub_domain` VARCHAR(128) NOT NULL COMMENT '主机记录(RR)',
  `domain_name` VARCHAR(128) NOT NULL COMMENT '域名名称',
  `public_ip` VARCHAR(64) NOT NULL COMMENT '解析到的IP',
  `type` VARCHAR(10) DEFAULT 'A' COMMENT '解析类型',
  `status` VARCHAR(20) DEFAULT 'Enable' COMMENT '状态: Enable/Disable',
  `line` VARCHAR(20) DEFAULT 'default',
  `ttl` INT DEFAULT 600,
  `weight` INT DEFAULT 1,
  `description` TEXT,
  `cluster_name` VARCHAR(128) DEFAULT NULL,
  `create_time` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_record_id` (`record_id`),
  INDEX `idx_email_cluster` (`email`, `cluster_id`),
  INDEX `idx_sub_domain` (`sub_domain`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```
```
Corefile文件
. {
    etcd {
        path /skydns
        endpoint http://localhost:2379
        fallthrough
    }
    log
    errors
    forward . 8.8.8.8 114.114.114.114
}
```
```
docker run -d \
  --name coredns \
  --restart always \
  -p 53:53/udp \
  -p 53:53/tcp \
  -v /opt/mysql/dnsconf/Corefile:/etc/coredns/Corefile \
  coredns/coredns:latest \
  -conf /etc/coredns/Corefile
```
  验证请求
  ```
  curl -X GET "http://127.0.0.1:8000/?Action=AddDomainRecord&DomainName=test.cog&RR=dev&Type=A&Value=1.2.3.4"
  curl -X GET "http://127.0.0.1:8000/?Action=DescribeSubDomainRecords&SubDomain=dev.test.cog"
  curl -X GET "http://127.0.0.1:8000/?Action=UpdateDomainRecord&RecordId=10832296992754&RR=dev&Value=5.6.7.8"
  curl -X GET "http://127.0.0.1:8000/?Action=SetDomainRecordStatus&RecordId=10832296992754&Status=Disable"
  curl -X GET "http://127.0.0.1:8000/?Action=SetDomainRecordStatus&RecordId=10832296992754&Status=Enable"
  curl -X GET "http://127.0.0.1:8000/?Action=DeleteSubDomainRecords&DomainName=test.cog&RR=dev"
```
  dig @127.0.0.1 dev.test.cog
  
