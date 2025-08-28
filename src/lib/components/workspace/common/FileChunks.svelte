<script lang="ts">
	import { getContext, onMount } from 'svelte';

	const i18n = getContext('i18n');

	// import { getGroups } from '$lib/apis/groups';
	import { getFileChunksById } from '$lib/apis/files';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import UserCircleSolid from '$lib/components/icons/UserCircleSolid.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Badge from '$lib/components/common/Badge.svelte';

	// export let onChange: Function = () => {};

	// export let accessControl = {};

	// export let allowPublic = true;

	export let selectedFileId = '';
	let selectedFileName = '';
	let chunks = [];
	let res = {};
	const colorsArr = ["#2a9d8f","#e9c46a","#f4a261","#e76f51"]; //"#264653"
	// const colorsArr = ["#FFFF66","#FFE566","#D6D58B","#B3B347"];
	// const colorsArr = ["#FDF5E6", "#E0E0E0"];

	// $: if (!allowPublic && accessControl === null) {
	// 	accessControl = {
	// 		read: {
	// 			group_ids: [],
	// 			user_ids: []
	// 		},
	// 		write: {
	// 			group_ids: [],
	// 			user_ids: []
	// 		}
	// 	};
	// 	onChange(accessControl);
	// }
	// $: if (!allowPublic && accessControl === null) {
	// 	return {};
	// }

	onMount(async () => {
		res = await getFileChunksById(localStorage.token, selectedFileId);
		chunks = res.chunks;
		selectedFileName = res.filename;
	});
</script>

<div class=" rounded-lg flex flex-col gap-2">
	{#key chunks}
		{selectedFileName = res.filename ?? ''}
		<div>
			<div class="">
				<!-- <div class="flex justify-between mb-1.5">
					<div class="text-sm font-semibold">
						{$i18n.t({selectedFileName})}
					</div>
				</div> -->

				<hr class=" border-gray-100 dark:border-gray-700/10 mt-1.5 mb-2.5 w-full" />

				<div class="flex flex-col mb-1 gap-1.5 px-0.5">
					{#if chunks.length > 0}
						{#each chunks as chunk, i}
							<div data-chunk-no="{i}" style="background-color:{colorsArr[i % colorsArr.length]}; color: #36454F;" class="text-sm w-full transition"> <!-- gap-3 justify-between text-xs w-full transition -->
								{chunk}
							</div>
						{/each}
					{:else}
						<div class="flex items-center justify-center">
							<div class="text-gray-500 text-xs text-center py-2 px-10">
								{$i18n.t('Document chunks are unavailable; please try again.')}
							</div>
						</div>
					{/if}
				</div>
			</div>
		</div>
	{/key}
</div>
