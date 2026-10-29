# rmw_zenoh_config

Ubuntu 22.04 / ROS 2 Humble向けのrmw_zenoh router導入・更新用リポジトリ。
中央PCと移動体用PCのROS_DOMAIN_IDは35。各PCにrouterを1つ置き、中央PCに明示接続する構成。

## 前提

- ROS Humble導入済み。全PCでrmw_zenohのバージョンをそろえる。
- 中央VMへのTCP 7447と戻り通信が許可されている。
- NTPによる時刻同期が正常。
- 旧zenoh-bridgeや手動routerが7447を使用していない。
- make install/updateはrouterを起動・再起動する。通信停止可能な時間に実施する。

```bash
sudo apt update
sudo apt install make git python3 iproute2 ros-humble-rmw-zenoh-cpp ros-humble-demo-nodes-cpp netcat-openbsd
```

## 環境設定（初回・変更時）

`config/deployment.env` は初めから配置されている。コピーは不要。

`config/deployment.env` を各PCの環境に合わせて編集する。

```bash
SERVER=192.168.1.70
ROS_DOMAIN_ID=35
```

| 項目 | 内容 |
|---|---|
| SERVER | 中央PCのIPv4アドレス。中央PC自身にも記載する |
| ROS_DOMAIN_ID | ROSノードの通信グループ。同じグループのPCで統一する |

2項目とも必須。ROLE・RUN_USER・旧名DOMAIN_IDはこのファイルに記載しない。
ROS_DOMAIN_IDは0〜232の整数。シェルコマンド・変数展開は使わず、値を直接記載する。
`config/deployment.env` は初期値入りでGit管理する。各PCで編集した値を誤ってcommit・pushしないこと。GitHub側で同じファイルが変更されると、取得時に競合する場合がある。

**このローカルファイルが編集元。`/etc/zenoh/rmw_zenoh_config/deployment.env` は自動配置先なので、直接編集しない。**
install/updateで値を検証して配置し、不正・不足の場合は配置先を上書きせずエラーにする。

## インストール

通常ユーザーで実行する。`sudo make`は使用しない。必要な処理だけ内部でsudoを使う。

**実行前に`<対象機械>`を、使用する`profiles/<機器名>.json`のファイル名（拡張子なし）へ置き換える。**
山括弧は含めず、以下のプロファイル名を指定する。以下の山括弧付き表記は説明用で、そのまま実行しない。

| 対象機械・プロファイル名 | ファイル |
|---|---|
| `d37pxi` | [profiles/d37pxi.json](profiles/d37pxi.json) |
| `zx200` | [profiles/zx200.json](profiles/zx200.json) |
| `mst110cr` | [profiles/mst110cr.json](profiles/mst110cr.json) |
| `control`（制御PC用） | [profiles/control.json](profiles/control.json) |

[profiles/template.json](profiles/template.json)は、新しい機器のプロファイルを作るためのひな形。対象機械の一覧には含めない。

```bash
# 中央PC：機器指定なし
make install ROLE=central

# 車載PC：搭載する建機を指定
make install ROLE=vehicle DEVICE="<対象機械>"

# 制御PC：制御PC専用プロファイルを指定
make install ROLE=control
```

vehicleはDEVICEが必須で、自機のプロファイルを選ぶ。controlはDEVICEを省略するとcontrol.jsonを自動選択する。選択は配置先のselected-profilesに記録する。
制御PCで別のプロファイルを使う場合のみ、make install ROLE=control DEVICE="<プロファイル名>"で明示指定する。
従来の複数プロファイル指定も利用可能。DEVICEにダブルクォート付きスペース区切りで指定する。
旧引数DEVICESも互換性のため受け付けるが、DEVICEとの同時指定はエラー。
centralではDEVICE/DEVICESを指定しない。
RUN_USERは省略可能。省略時はコマンドを実行したユーザーでrouterを動かす。
詳細は下部の補足を参照。

中央は0.0.0.0:7447で待受、ほかは127.0.0.1:7447で待受し中央へ接続する。
multicast無効、gossip有効、multihop無効。
初回導入時にインストール済みROSパッケージの標準router設定をコピーする。
SERVER・ROS_DOMAIN_IDはローカルenvから配置する。ROLEとRUN_USERはinstall時に決定し、配置先に記録する。

## ROSノードの環境

各ユーザーの `~/.bashrc` の末尾へ一度だけ追加する。旧 `~/zenoh/ros-env.sh` の読み込み行は置き換える。

```bash
if [ -f /etc/zenoh/rmw_zenoh_config/ros-env.sh ]; then
    source /etc/zenoh/rmw_zenoh_config/ros-env.sh
fi
```

```bash
source ~/.bashrc
printenv RMW_IMPLEMENTATION ROS_DOMAIN_ID
ros2 daemon stop
```

期待値：rmw_zenoh_cpp / envで指定したROS_DOMAIN_ID（初期値は35）。既存ROSノードは再起動する。
ローカルrouter未起動時はROSノードが起動待ちになる。
ROSノードのsystemd自動起動は本リポジトリの対象外。
そのサービスではROSノード起動前に上記ros-env.shとワークスペースを明示的にsourceする。
必要に応じてUnitに `Requires=rmw-zenoh-router.service` と `After=rmw-zenoh-router.service` を指定する。

## 設定・コードの更新

ローカルの `config/deployment.env`、選択済みの `profiles/*.json`、またはスクリプトを編集した後に実行する。

```bash
make update
```

- envのSERVER・ROS_DOMAIN_ID、記録済みプロファイル、スクリプト・サービスをまとめて反映しrouterを再起動する。
- ROLEとRUN_USERはinstall時の値を維持する。別ユーザーがupdateしても実行ユーザーは変わらない。
- router.json5とプロファイルの選択は保持する。プロファイルの中身は作業フォルダのJSONで更新する。
- 編集元はローカルリポジトリ。配置先のenvやdeny-profile.jsonを直接編集しても、updateで上書きされる。
- ROS_DOMAIN_IDが変わると、プロファイルのACLも新Domainのキーで実行時に再生成される。
- ROSパッケージ更新は行わない。

GitHubからの取得は必須ではない。各PCでローカルに編集した内容をそのまま反映できる。
GitHub上のコードを取り込む場合だけ、ローカル編集との競合を確認して別途取得する。

**ROS_DOMAIN_ID変更後は、各ROSノードも環境を再読み込みして再起動する。**

```bash
source /etc/zenoh/rmw_zenoh_config/ros-env.sh
ros2 daemon stop
# ここでROSノードを再起動。systemd起動のROSノードも再起動する。
```

ROLEを変える場合は設定をバックアップし、make purge後に新しいROLEでinstallする。

### プロファイルの選び直し

通常はinstall時に記録したプロファイルを使うため、make updateにDEVICEを指定する必要はない。選択を変更する場合のみ指定する。

```bash
# 車載PC
make update DEVICE="<対象機械>"
# 制御PC
make update DEVICE="control"
```

変更後はmake updateだけで同じプロファイルを再適用する。
不明なプロファイル・不正なJSONを指定した場合は、既存設定を上書きせずエラーにする。
中央PCはプロファイルを使わないため、make updateだけでよい。


| 変更する内容 | 編集元 | 反映コマンド |
|---|---|---|
| SERVER・ROS_DOMAIN_ID | config/deployment.env | make update |
| スクリプト・サービス | scripts/・systemd/ | make update |
| allow・denyリスト | 選択済みのprofiles/*.json | make update |

## routerサービスの操作

インストール後はsystemdでrouterを管理する。手動で起動スクリプトを実行すると二重起動になるため、以下のコマンドを使う。

### 状態確認

```bash
systemctl status rmw-zenoh-router.service
```

`Active: active (running)`なら稼働中。

### 停止・起動

```bash
# 停止
sudo systemctl stop rmw-zenoh-router.service

# 起動
sudo systemctl start rmw-zenoh-router.service
```

停止すると、このrouterを介したPC間通信も停止する。中央PCのrouterを停止した場合は、中央を経由する各PC間の通信が停止する。

### 再起動

```bash
sudo systemctl restart rmw-zenoh-router.service
```

再起動すると、配置済みの設定を読み直す。通信制限を設定している場合は、中央への経路から対象NICも再判定する。

ローカルリポジトリで編集した設定を反映する場合は、再起動だけではなく、次を使う。

`config/deployment.env`・選択済みの`profiles/*.json`の変更は、どちらも`make update`で反映しrouterを再起動する。

### ログ確認

```bash
# 今回のPC起動以降の直近50行
journalctl -u rmw-zenoh-router.service -b -n 50 --no-pager

# ログを継続表示（Ctrl+Cで表示終了）
journalctl -u rmw-zenoh-router.service -f
```

権限不足で表示できない場合は先頭にsudoを付ける。ログ表示をCtrl+Cで終了しても、サービスは停止しない。

### 自動起動の無効化・有効化

stopだけでは自動起動設定は変わらず、次のPC起動時にはrouterも起動する。

```bash
# 今すぐ停止し、次回以降の自動起動も無効化
sudo systemctl disable --now rmw-zenoh-router.service

# 自動起動を有効化し、今すぐ起動
sudo systemctl enable --now rmw-zenoh-router.service
```

`make install`・`make update`を実行すると、自動起動も再び有効になる。

## 削除

```bash
# サービスと実行ファイルを削除。配置済み設定・ROS環境ファイルは保持
make uninstall
# サービス・実行ファイルに加え、配置済み設定ディレクトリも削除
make purge
```

削除時は.bashrcのsource行も手動で削除する。ROSパッケージ、旧bridge、ほかの/etc/zenoh配下のファイルには触れない。
ローカルリポジトリのconfig/deployment.envやprofilesは削除しない。
uninstall後の再導入はmake updateで行える。ローカルenv・記録済みプロファイルを再反映し、ROLE・RUN_USERを保持する。

## 機器別allow・denyリスト

`profiles/<機器名>.json` に機器別の通信制限を記載する。
車載用の`d37pxi.json`・`zx200.json`・`mst110cr.json`、制御PC用の`control.json`、新規プロファイル用の`template.json`を用意している。
**配布プロファイルはすべて空であり、初期状態ではこのツールによる通信制限を追加しない。**
車載側と制御側は別ファイルで独立して管理する。control.jsonには制御PCで必要な各建機の通信をまとめる。同じルールを両側へ自動複製しない。PC間の通信は両端で許可されている必要がある。

```json
{
  "deny": {
    "topics": [],
    "services": [],
    "actions": []
  },
  "allow": {
    "topics": [],
    "services": [],
    "actions": []
  }
}
```

- 各配布JSONの`_comment`は説明用で、通信ルールとしては評価しない。
- 優先順位は明示的deny > 明示的allow > 既定値。deny → allowの表示順は読みやすさのためであり、並び順で判定は変わらない。
- topics：ROS Topicの絶対名。
- services：ROS Serviceの絶対名。
- actions：ROS Actionの絶対名。関連する5つの通信と探索情報もツール側で扱う。
- allow・denyがすべて空なら制限追加なし。名前のワイルドカード指定は未対応。
- **allowがすべて空なら基本許可でdenyだけ遮断する。allowに1件でも記載すると、中央との通信は合算したallow一覧に限定する。両方に一致する通信はdenyが優先する。**
- denyは中央への経路のNICにだけ適用し、localhost側の通信は維持する。
- 名前は種類間で重複する可能性があるため、denyは指定名に一致するデータ・探索キーに適用する。通常のROS命名でTopicとServiceに同じ完全名を使う構成は避ける。

### allow・denyの組み合わせと動作

中央との通信は、次の4パターンで判定する。「空」は、その側のtopics・services・actionsがすべて空配列であることを指す。

| deny | allow | 中央との通信 |
|---|---|---|
| 空 | 空 | 既定値の全許可。このツールによる制限は追加しない |
| 記載あり | 空 | deny対象を拒否。それ以外は既定値の全許可 |
| 空 | 記載あり | allow対象のみ許可。それ以外は拒否 |
| 記載あり | 記載あり | allow対象のみ許可。ただしdenyにも一致する通信は拒否。それ以外も拒否 |

**優先順位はdeny > allow > 既定値。allowでdenyを例外解除することはできない。**
localhost内の通信は、どのパターンでも維持する。ROSの探索に必要な補助通信はツール側で扱う。
両方が空の場合は生成ACLを追加しないため、router.json5に手書きのACLがある場合は、その設定が引き続き適用される。

複数の機器プロファイルを指定した場合は、全プロファイルのdeny・allowをそれぞれ合算し、重複を除去してから上表を適用する。
**1つでもallowに記載があれば、ほかの機器も含めて合算したallow一覧にない通信は拒否される。**

### 設定例

例えば生ActionをPC内に限定する場合、deny.actionsに記載する。

```json
{
  "deny": {
    "topics": [],
    "services": [],
    "actions": ["/d37pxi/navigate_to_pose"]
  },
  "allow": {"topics": [], "services": [], "actions": []}
}
```

この例ではallowが空なので `/d37pxi/zenoh/navigate_to_pose` は禁止しない。

共通Actionだけを中央との通信で許可する場合、allow.actionsへ `/d37pxi/zenoh/navigate_to_pose` を記載する。
この場合、一覧にない状態Topic・パラメータServiceなども中央との間では遮断される。必要なら明示的に追加する。
localhost内の通信は維持する。
サンプルは説明用であり、配布プロファイルには自動反映しない。

機器プロファイルは通信制限を選ぶ設定。接続先はSERVER、ROS通信グループはROS_DOMAIN_IDで決まる。
複数機器のプロファイルは合算し、重複を除去する。**いずれかの機器にallowがあれば、中央との通信は合算後のallow一覧に限定される。他の機器についても必要な通信をallowに記載すること。**名前の間違いや不正な形式はエラーにする。

### プロファイルの編集・反映

install時に選んだローカルのJSONを編集する。

- 車載PC：profiles/d37pxi.jsonなど、自機のプロファイル。
- 制御PC：profiles/control.json。

```bash
make update
```

**ROS_DOMAIN_IDも変更した場合は、ROSノードを起動する各ターミナルで環境を再読み込みする。**
（上記のROSノード用環境設定を.bashrcへ追加済みであること。）

```bash
source ~/.bashrc
```

起動済みのROSノードには反映されないため、ノードも再起動する。systemdで起動しているROSノードは、そのサービスを再起動する。


機器名の再指定やGitHubからの取得は毎回必要ない。記録済みプロファイルを読み直し、env・コードと一緒に反映してrouterを再起動する。
従来の複数プロファイルを使っているPCも、記録済みの全プロファイルを読み直す。
プロファイルを選び直す場合だけmake update DEVICE="<プロファイル名>"を使う。指定した一式で選択を置き換えるため、複数を維持する場合はすべて指定する。

適用前のプロファイルはdeny-profile.json.bakへ保存する（前回バックアップは上書き）。
**空プロファイルの反映は、このツールで設定していたallow・deny制限の解除になる。**
updateはrouterを再起動するため、通信が一時中断する。
適用先は `/etc/zenoh/rmw_zenoh_config/deny-profile.json`。直接編集せず、ローカルのprofiles内を編集する。
旧形式のdeny_actionsも読み込める。

新規機器はtemplate.jsonをコピーして作成する。

```bash
cp profiles/template.json profiles/my_machine.json
```

対応キー形式はROS Humble rmw_zenoh 0.1.11。生成ACLは起動時のZENOH_CONFIG_OVERRIDEで適用する。
localhostのlo接続はallowモードでも維持する。allowモードでは必要なROSノードの探索情報と探索要求も許可するが、一覧にないTopic/Service/Actionの実データ・エンティティ探索は許可しない。
空プロファイルはACLを生成せず、router.json5のaccess_controlを変更しない。
非空のプロファイルはrouter.json5内のaccess_control全体を置き換えるため、手書きACLとのマージは未対応。

### NIC自動取得とネットワーク変更

NICはinstall時ではなく **router起動・再起動時** に判定する。
`deployment.env` のSERVERを読み、`ip -j -4 route get SERVER` によりOSが選ぶ経路のdevを取得する。
サーバへの通信でNICを探索する処理ではない。lo・ローカルIP・経路不明はエラーにし、ACLなしでは起動しない。
プロファイル適用時のSERVERはIPv4アドレスを指定する。

- 同じSERVERで別NWへ移動：makeの再実行は不要。サービス再起動でNICを再判定する。
- SERVERのIPが変わった：ローカルのconfig/deployment.envのSERVERを編集し、make updateを実行する。
- 稼働中にNICが変わった：監視で検出するとrouterを終了し、systemdの再試行でACLを再生成する。
- 同じNICで経路が変わった：ACL対象NICは変わらない。接続断は以下の監視対象になる。

```bash
sudo systemctl restart rmw-zenoh-router.service
```

自動判定はACLの対象NICを選ぶだけで、ソケットをNICにbindする処理ではない。
複数経路やVPNを使う場合は切替試験を実施すること。切替検出までの監視間隔は最大10秒程度あるため、厳密な分離境界として扱う場合は別途ネットワーク・認証設計が必要。
中央と同じNICを通るほかのZenoh接続にも、このdenyルールは適用される。

### 中央への到達性と停止・再試行

vehicle/controlは起動前だけTCP到達性を確認する（プロファイル未指定の場合も対象）。centralは自己確認しない。

- 起動前：SERVERのTCP 7447へ接続できなければrouterを起動せず失敗終了する。
- 稼働中：中央へのTCP接続確認は行わない。無線切断や中央停止だけを理由にrouterを終了しない。再接続はZenohに任せる。
- systemdが5秒後に再試行する。中央復旧後にrouterを起動する。
- denyプロファイル適用中のみ、10秒間隔で経路NICを確認する。別NICへの変更が確認できた場合だけ停止・再生成する。経路が一時的に取得できない場合は稼働を継続する。

**中央切断時もローカルrouterは維持する。** NIC変更による再生成時はrouterの再起動が発生する。機体の安全停止はこのツールの対象外。
起動前の確認はTCPポートへの到達性であり、Zenohセッション・ROS通信の健全性の保証ではない。
起動前のプローブはTCP接続後すぐ切断するため、中央側に未完了ハンドシェイクのログが出る場合がある。

### 適用後の検証

実機Ubuntu上で、車載PCと制御PCの両側で確認する。

1. routerログの `ACL: SERVER=..., interface=...` が実際の経路と一致する。
2. 生Actionの遠隔エンドポイントが発見されず、各PC内のAction接続は動作する。
3. 共通Action経由でGoal/Feedback/Result/Cancelが往復する。
4. 他Topicの通信は維持する。
5. 同じNICでネットワークを切断してもrouterが維持され、復旧後に再接続する。起動前から中央に到達できない場合は起動を再試行する。
6. NIC変更後に対象NICが再判定される。

同名Actionの一覧表示だけでは遠隔遮断の証明にならない。ROSノード情報と、制御されたテスト用Actionサーバで通信先を確認する。実機を動かすGoalを疎通確認だけのために送らない。

プロファイルを解除する場合（denyが解除される）：

```bash
sudo mv /etc/zenoh/rmw_zenoh_config/deny-profile.json /etc/zenoh/rmw_zenoh_config/deny-profile.json.disabled
sudo systemctl restart rmw-zenoh-router.service
```

## 動作確認

```bash
systemctl is-enabled rmw-zenoh-router.service
systemctl status rmw-zenoh-router.service
journalctl -u rmw-zenoh-router.service -b -n 50 --no-pager
sudo ss -lntp 'sport = :7447'
```

車載・制御PCから：

```bash
nc -vz -w 3 192.168.1.70 7447
ss -ntp dst 192.168.1.70:7447
```

ESTABはTCP接続の確認。さらに別PC間でtalker/listenerを実行する。
各ターミナルでROS環境を読み込んだ状態で、片側は `ros2 run demo_nodes_cpp talker`、もう片側は `ros2 run demo_nodes_cpp listener`。
役割を入れ替えて双方向を確認し、PC再起動後も再確認する。

時刻エラーが出たら `timedatectl status` を確認する。大きな時刻補正前は制御ノードを停止し、補正後にrouterとROSノードを再起動する。

## 通信障害の調査用ログ

[ログ取得スクリプトの使い方・保存内容・調査手順](docs/zenoh-capture.md)

## 参考

- [rmw_zenoh Humble](https://github.com/ros2/rmw_zenoh/tree/humble)
- [Zenoh access control](https://zenoh.io/docs/manual/access-control/)

本リポジトリの実機検証は利用先Ubuntuで行う。最新値優先のQoS、指令の有効期限、切断時の安全処理はアプリ側でも設計する。

キー形式・ACLの確認元：
- [rmw_zenoh 0.1.11 liveliness_utils.cpp](https://github.com/ros2/rmw_zenoh/blob/0.1.11/rmw_zenoh_cpp/src/detail/liveliness_utils.cpp)
- [rmw_zenoh 0.1.11 vendor](https://github.com/ros2/rmw_zenoh/blob/0.1.11/zenoh_cpp_vendor/CMakeLists.txt)

ローカルテストは生成キー・ルート変更・到達不能時の停止処理を検証する。ROS/Zenohを含む実機統合試験は未実施。

## 補足：サービス実行ユーザー（RUN_USER）

RUN_USERはrouterを動かすLinuxユーザーを指定する。ROSの通信相手・建機の識別には使わない。
省略時はmakeを実行したユーザーを使う。

```bash
# bullでログインしている場合、次の2つは同じ
make install ROLE=vehicle DEVICE="<対象機械>"
make install ROLE=vehicle DEVICE="<対象機械>" RUN_USER=bull
```

インストールだけ管理者権限を使い、router自体は指定ユーザーで動作する。
systemdのUserは指定ユーザー、Groupはそのプライマリグループになる。
rootは指定不可。別ユーザーを指定する場合は、そのPC上に存在するユーザーを選ぶ。

```bash
make install ROLE=vehicle DEVICE="<対象機械>" RUN_USER=operator
```

RUN_USERはrouterサービス用。ROSノードを同じユーザーで動かす必要はないが、各ノードでRMW・Domain ID・接続設定をそろえる。
systemdはユーザーの.bashrcを読み込まないため、ROSノード用サービスではros-env.shを明示的に読み込む。

### 中央ルータの送信待ち時間

`ROLE=central` は起動スクリプトの `ZENOH_CONFIG_OVERRIDE` で
`transport/link/tx/queue/congestion_control/block/wait_before_close=15000000`
（15秒）を適用する。無線区間の一時的な送信停滞を待つための設定で、
中央から相手への送信にのみ適用する。車体・制御PCの値は変更しない。
`make install` / `make update` で起動スクリプトが配置され、再起動時に適用される。
`make update` は配置済み `router.json5` を保持するが、この項目は起動時の上書きを優先する。
変更する場合は `scripts/start-rmw-zenoh-router` の値を編集する。
