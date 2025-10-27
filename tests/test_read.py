import datetime
import io
from itertools import chain

import pytest

# Test data is now provided via fixtures in conftest.py


def test_ls(sample_fs):
    fs = sample_fs
    assert fs.ls("/", False) == [
        "/csv",
        "/emptydir",
        "/nested",
        "/test",
        "/file.dat",
        "/filexdat",
    ]
    assert fs.ls("/test", False) == ["/test/accounts.1.json", "/test/accounts.2.json"]
    assert fs.ls("/test/accounts.1.json", False) == ["/test/accounts.1.json"]
    assert fs.ls("/nested", False) == [
        "/nested/nested2",
        "/nested/file1",
        "/nested/file2",
    ]
    assert fs.ls("/nested/nested2", False) == [
        "/nested/nested2/file1",
        "/nested/nested2/file2",
    ]
    assert fs.ls("/file.dat", False) == ["/file.dat"]
    assert fs.ls("/emptydir", False) == []


@pytest.mark.asyncio(loop_scope="module")
async def test_async_ls(sample_afs):
    fs = sample_afs
    assert await fs._ls("/", False) == [
        "/csv",
        "/emptydir",
        "/nested",
        "/test",
        "/file.dat",
        "/filexdat",
    ]
    assert await fs._ls("/test", False) == [
        "/test/accounts.1.json",
        "/test/accounts.2.json",
    ]
    assert await fs._ls("/test/accounts.1.json", False) == ["/test/accounts.1.json"]
    assert await fs._ls("/nested", False) == [
        "/nested/nested2",
        "/nested/file1",
        "/nested/file2",
    ]
    assert await fs._ls("/nested/nested2", False) == [
        "/nested/nested2/file1",
        "/nested/nested2/file2",
    ]
    assert await fs._ls("/file.dat", False) == ["/file.dat"]
    assert await fs._ls("/emptydir", False) == []


def test_ls_detail(sample_fs):
    fs = sample_fs
    assert fs.ls("/", True)[0]["type"] == "directory"
    assert fs.ls("/test/accounts.1.json", True)[0]["type"] == "file"
    assert fs.ls("file.dat", True)[0]["type"] == "file"


@pytest.mark.parametrize(
    "path, expected_type, expected_size, expected_name, expected_mimetype",
    [
        ("/", "directory", 0, "/", None),
        ("/test", "directory", 0, "/test", None),
        (
            "/test/accounts.1.json",
            "file",
            133,  # pre-calculated length from test data
            "/test/accounts.1.json",
            "application/json",
        ),
    ],
)
def test_info(
    sample_fs,
    all_test_data,
    path,
    expected_type,
    expected_size,
    expected_name,
    expected_mimetype,
):
    fs = sample_fs
    file_info = fs.info(path)
    assert file_info["type"] == expected_type
    if expected_type == "file":
        # size for directories is not computed synchronously
        assert file_info["size"] == expected_size
    assert file_info["name"] == expected_name if expected_name != "/" else fs.path
    if expected_type == "file":
        assert file_info["mimetype"] == expected_mimetype

    # date are today
    assert (
        file_info["mtime"] is not None
        and file_info["mtime"].date() == datetime.datetime.now().date()
    )
    assert (
        file_info["time"] is not None
        and file_info["time"].date() == datetime.datetime.now().date()
    )


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize(
    "path, expected_type, expected_size, expected_name, expected_mimetype",
    [
        ("/", "directory", 0, "/", None),
        ("/test", "directory", 0, "/test", None),
        (
            "/test/accounts.1.json",
            "file",
            133,  # pre-calculated length from test data
            "/test/accounts.1.json",
            "application/json",
        ),
    ],
)
async def test_async_info(
    sample_afs, path, expected_type, expected_size, expected_name, expected_mimetype
):
    fs = sample_afs
    file_info = await fs._info(path)
    assert file_info["type"] == expected_type
    if expected_type == "file":
        # size for directories is not computed synchronously
        assert file_info["size"] == expected_size
    assert file_info["name"] == expected_name if expected_name != "/" else fs.path
    if expected_type == "file":
        assert file_info["mimetype"] == expected_mimetype

    # date are today
    assert (
        file_info["mtime"] is not None
        and file_info["mtime"].date() == datetime.datetime.now().date()
    )
    assert (
        file_info["time"] is not None
        and file_info["time"].date() == datetime.datetime.now().date()
    )


@pytest.mark.xfail(reason="The cache is not working at the moment but it should")
def test_info_cached(sample_fs):
    fs = sample_fs
    file_info = fs.fs.info(fs._join("/test/accounts.1.json"))
    cached_info = fs.fs.info(fs._join("/test/accounts.1.json"))
    assert file_info == cached_info
    assert file_info is cached_info


def test_checksum(sample_fs):
    fs = sample_fs
    checksum = fs.checksum("/test")
    assert checksum is not None
    # add content to test
    fs.touch("/test/accounts.3.json")
    checksum2 = fs.checksum("/test")
    try:
        assert checksum != checksum2
    finally:
        fs.rm("/test/accounts.3.json")


@pytest.mark.asyncio(loop_scope="module")
async def test_async_checksum(sample_afs):
    fs = sample_afs
    checksum = await fs.fs._checksum(fs._join("/test"))
    assert checksum is not None
    # add content to test
    await fs.fs._touch(fs._join("/test/accounts.3.json"))
    checksum2 = await fs.fs._checksum(fs._join("/test"))
    try:
        assert checksum != checksum2
    finally:
        await fs._rm("/test/accounts.3.json")


def test_isdir(sample_fs):
    fs = sample_fs
    assert fs.isdir("/test")
    assert fs.isdir("/nested")
    assert not fs.isdir("/test/accounts.1.json")
    assert not fs.isdir("/test/unknwown")
    assert not fs.isdir("/file.dat")


@pytest.mark.asyncio(loop_scope="module")
async def test_async_isdir(sample_afs):
    fs = sample_afs
    assert await fs._isdir("/test")
    assert await fs._isdir("/nested")
    assert not await fs._isdir("/test/accounts.1.json")
    assert not await fs._isdir("/test/unknwown")
    assert not await fs._isdir("/file.dat")


def test_isfile(sample_fs):
    fs = sample_fs
    assert not fs.isfile("/test")
    assert not fs.isfile("/nested")
    assert fs.isfile("/test/accounts.1.json")
    assert fs.isfile("/file.dat")
    assert not fs.isfile("/unknwown")


@pytest.mark.asyncio(loop_scope="module")
async def test_async_isfile(sample_afs):
    fs = sample_afs
    assert not await fs._isfile("/test")
    assert not await fs._isfile("/nested")
    assert await fs._isfile("/test/accounts.1.json")
    assert await fs._isfile("/file.dat")
    assert not await fs._isfile("/unknwown")


def test_du(sample_fs, all_test_data):
    fs = sample_fs
    assert fs.du("/test") == sum(
        [
            len(all_test_data["files"]["test/accounts.1.json"]),
            len(all_test_data["files"]["test/accounts.2.json"]),
        ]
    )

    assert fs.du("/nested") == sum(
        [
            len(all_test_data["text_files"]["nested/file1"]),
            len(all_test_data["text_files"]["nested/file2"]),
            len(all_test_data["text_files"]["nested/nested2/file1"]),
            len(all_test_data["text_files"]["nested/nested2/file2"]),
        ]
    )
    assert fs.du("/file.dat") == len(all_test_data["glob_files"]["file.dat"])

    assert fs.du("/emptydir") == 0


@pytest.mark.asyncio(loop_scope="module")
async def test_async_du(sample_afs, all_test_data):
    fs = sample_afs
    assert await fs._du("/test") == sum(
        [
            len(all_test_data["files"]["test/accounts.1.json"]),
            len(all_test_data["files"]["test/accounts.2.json"]),
        ]
    )

    assert await fs._du("/nested") == sum(
        [
            len(all_test_data["text_files"]["nested/file1"]),
            len(all_test_data["text_files"]["nested/file2"]),
            len(all_test_data["text_files"]["nested/nested2/file1"]),
            len(all_test_data["text_files"]["nested/nested2/file2"]),
        ]
    )
    assert await fs._du("/file.dat") == len(all_test_data["glob_files"]["file.dat"])

    assert await fs._du("/emptydir") == 0


def test_glob(sample_fs):
    fs = sample_fs
    fn = "/nested/file1"
    assert fn not in fs.glob("/")
    assert fn not in fs.glob("/*")
    assert fn not in fs.glob("/nested")
    assert fn in fs.glob("/nested/*")
    assert fn in fs.glob("/nested/file*")
    assert fn in fs.glob("/*/*")
    assert all(
        any(p.startswith(f + "/") or p == f for p in fs.find("/"))
        for f in fs.glob("/nested/*")
    )
    assert ["/nested/nested2"] == fs.glob("/nested/nested2")
    out = fs.glob("/nested/nested2/*")
    assert {"/nested/nested2/file1", "/nested/nested2/file2"} == set(out)

    assert fs.glob("/nested/*") == ["/nested/file1", "/nested/file2", "/nested/nested2"]
    assert fs.glob("/nested/nested2/*") == [
        "/nested/nested2/file1",
        "/nested/nested2/file2",
    ]
    assert fs.glob("/*/*.json") == ["/test/accounts.1.json", "/test/accounts.2.json"]


@pytest.mark.asyncio(loop_scope="module")
async def test_async_glob(sample_afs):
    fs = sample_afs
    fn = "/nested/file1"
    assert fn not in await fs._glob("/")
    assert fn not in await fs._glob("/*")
    assert fn not in await fs._glob("/nested")
    assert fn in await fs._glob("/nested/*")
    assert fn in await fs._glob("/nested/file*")
    assert fn in await fs._glob("/*/*")
    nesteds = await fs._glob("/nested/*")
    alls = await fs._find("/")
    assert all(any(p.startswith(f + "/") or p == f for p in alls) for f in nesteds)
    assert ["/nested/nested2"] == await fs._glob("/nested/nested2")
    out = await fs._glob("/nested/nested2/*")
    assert {"/nested/nested2/file1", "/nested/nested2/file2"} == set(out)

    assert await fs._glob("/nested/*") == [
        "/nested/file1",
        "/nested/file2",
        "/nested/nested2",
    ]
    assert await fs._glob("/nested/nested2/*") == [
        "/nested/nested2/file1",
        "/nested/nested2/file2",
    ]
    assert await fs._glob("/*/*.json") == [
        "/test/accounts.1.json",
        "/test/accounts.2.json",
    ]


def test_seek(sample_fs):
    fs = sample_fs
    with fs.open("nested/file2", "rb") as f:
        f.seek(1000)
        with pytest.raises(ValueError):
            f.seek(-1)
        with pytest.raises(ValueError):
            f.seek(-6, 2)
        with pytest.raises(ValueError):
            f.seek(0, 10)
        f.seek(0)
        f.seek(0)
        assert f.read(1) == b"w"
        f.seek(0)
        assert f.read(1) == b"w"
        f.seek(2)
        assert f.read(1) == b"r"
        f.seek(-1, 2)
        assert f.read(1) == b"d"
        f.seek(-1, 1)
        f.seek(-1, 1)
        assert f.read(2) == b"ld"


@pytest.mark.asyncio(loop_scope="module")
async def test_async_seek(sample_afs):
    fs = sample_afs
    with await fs._open_async("nested/file2", "rb") as f:
        f.seek(1000)
        with pytest.raises(ValueError):
            f.seek(-1)
        with pytest.raises(ValueError):
            f.seek(-6, 2)
        with pytest.raises(ValueError):
            f.seek(0, 10)
        f.seek(0)
        f.seek(0)
        assert await f.read(1) == b"w"
        f.seek(0)
        assert await f.read(1) == b"w"
        f.seek(2)
        assert await f.read(1) == b"r"
        f.seek(-1, 2)
        assert await f.read(1) == b"d"
        f.seek(-1, 1)
        f.seek(-1, 1)
        assert await f.read(2) == b"ld"


def test_bad_open(sample_fs):
    fs = sample_fs
    with pytest.raises(FileNotFoundError):
        fs.open("/test", "r")


@pytest.mark.asyncio(loop_scope="module")
async def test_async_bad_open(sample_afs):
    fs = sample_afs
    with pytest.raises(FileNotFoundError):
        await fs.fs.open_async(fs._join("/test"), "r")


def test_readline(sample_fs, all_test_data):
    fs = sample_fs
    all_items = chain.from_iterable(
        [
            all_test_data["files"].items(),
            all_test_data["csv_files"].items(),
            all_test_data["text_files"].items(),
        ]
    )
    for k, data in all_items:
        with fs.open(f"/{k}", "rb") as f:
            result = f.readline()
            expected = data.split(b"\n")[0] + (b"\n" if data.count(b"\n") else b"")
            assert result == expected


def test_readline_empty(sample_fs):
    fs = sample_fs
    data = b""
    with fs.open("file.dat", "rb") as f:
        result = f.readline()
        assert result == data


def test_readline_blocksize(temp_fs):
    fs = temp_fs
    a = "/readline_blocksize"
    data = b"ab\n" + b"a" * (10 * 2**20) + b"\nab"
    with fs.open(a, "wb") as f:
        f.write(data)
    with fs.open(a, "rb") as f:
        result = f.readline()
        expected = b"ab\n"
        assert result == expected

        result = f.readline()
        expected = b"a" * (10 * 2**20) + b"\n"
        assert result == expected

        result = f.readline()
        expected = b"ab"
        assert result == expected


def test_next(sample_fs, all_test_data):
    path = "csv/2014-01-01.csv"
    expected = all_test_data["csv_files"][path].split(b"\n")[0] + b"\n"
    with sample_fs.open(path) as f:
        result = next(f)
        assert result == expected


def test_iterable(temp_fs):
    data = b"abc\n123"
    path = "/iterable"
    with temp_fs.open(path, "wb") as f:
        f.write(data)
    with temp_fs.open(path) as f, io.BytesIO(data) as g:
        for froms3, fromio in zip(f, g, strict=False):
            assert froms3 == fromio
        f.seek(0)
        assert f.readline() == b"abc\n"
        assert f.readline() == b"123"
        f.seek(1)
        assert f.readline() == b"bc\n"

    with temp_fs.open(path) as f:
        out = list(f)
    with temp_fs.open(path) as f:
        out2 = f.readlines()
    assert out == out2
    assert b"".join(out) == data


def test_readable(temp_fs):
    path = "/readable"
    with temp_fs.open(path, "wb") as f:
        assert not f.readable()

    with temp_fs.open(path, "rb") as f:
        assert f.readable()


def test_seekable(temp_fs):
    path = "/seekable"
    with temp_fs.open(path, "wb") as f:
        assert not f.seekable()

    with temp_fs.open(path, "rb") as f:
        assert f.seekable()


def test_writable(temp_fs):
    path = "/writable"
    with temp_fs.open(path, "wb") as f:
        assert f.writable()

    with temp_fs.open(path, "rb") as f:
        assert not f.writable()


def test_cat(sample_fs, all_test_data):
    fs = sample_fs
    all_items = chain.from_iterable(
        [
            all_test_data["files"].items(),
            all_test_data["csv_files"].items(),
            all_test_data["text_files"].items(),
        ]
    )
    for k, data in all_items:
        read = fs.cat(f"/{k}")
        assert read == data


@pytest.mark.asyncio(loop_scope="module")
async def test_async_cat(sample_afs, all_test_data):
    fs = sample_afs
    all_items = chain.from_iterable(
        [
            all_test_data["files"].items(),
            all_test_data["csv_files"].items(),
            all_test_data["text_files"].items(),
        ]
    )
    for k, data in all_items:
        read = await fs._cat(f"/{k}")
        assert read == data


def test_read_block(sample_fs, all_test_data):
    fs = sample_fs
    path = "csv/2014-01-01.csv"
    data = all_test_data["csv_files"][path]
    out = []
    with fs.open(path, "rb", block_size=3) as f:
        while True:
            block = f.read(20)
            out.append(block)
            if not block:
                break
    assert b"".join(out) == data


@pytest.mark.asyncio(loop_scope="module")
async def test_async_read_block(sample_afs, all_test_data):
    fs = sample_afs
    path = "csv/2014-01-01.csv"
    data = all_test_data["csv_files"][path]
    out = []
    async with await fs._open_async(path, "rb", block_size=3) as f:
        while True:
            block = await f.read(20)
            out.append(block)
            if not block:
                break
    assert b"".join(out) == data


def test_readinto(sample_fs, all_test_data):
    fs = sample_fs
    path = "csv/2014-01-01.csv"
    data = all_test_data["csv_files"][path]
    out = bytearray(len(data))
    with fs.open(path, "rb") as f:
        f.readinto(out)
    assert out == data


@pytest.mark.asyncio(loop_scope="module")
async def test_async_readinto(sample_afs, all_test_data):
    fs = sample_afs
    path = "csv/2014-01-01.csv"
    data = all_test_data["csv_files"][path]
    out = bytearray(len(data))
    async with await fs._open_async(path, "rb") as f:
        await f.readinto(out)
    assert out == data


def test_readuntil(sample_fs, all_test_data):
    fs = sample_fs
    path = "csv/2014-01-01.csv"
    data = all_test_data["csv_files"][path]
    out = []
    with fs.open(path, "rb") as f:
        while True:
            block = f.readuntil(b"\n")
            out.append(block)
            if not block:
                break
    assert b"".join(out) == data


def test_shallow_find(sample_fs):
    """Test that find method respects maxdepth.

    Verify that the ``find`` method respects the ``maxdepth`` parameter.  With
    ``maxdepth=1``, the results of ``find`` should be the same as those of
    ``ls``, without returning subdirectories.
    """
    path = "/"
    ls_output = sample_fs.ls(path, detail=False)
    assert sorted(ls_output + [path]) == sample_fs.find(path, maxdepth=1, withdirs=True)
    assert sorted(ls_output) == sample_fs.glob("/*")


@pytest.mark.asyncio(loop_scope="module")
async def test_async_shallow_find(sample_afs):
    path = "/"
    ls_output = await sample_afs._ls(path, detail=False)
    assert sorted(ls_output + [path]) == await sample_afs._find(
        path, maxdepth=1, withdirs=True
    )
    assert sorted(ls_output) == await sample_afs._glob("/*")


def test_ls_with_expand(sample_fs):
    fs = sample_fs
    infos = fs.ls("/csv", detail=True, expand="thumbnails")
    assert len(infos) == 3
    assert all("thumbnails" in info["item_info"] for info in infos)


def test_info_with_expand(sample_fs):
    fs = sample_fs
    info = fs.info("/csv", expand="thumbnails")
    assert "thumbnails" in info["item_info"]
